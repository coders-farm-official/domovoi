/* Stations page — the radio plugin's dashboard page.
 *
 * Loaded by the app bootstrap per the plugin manifest ([web].scripts),
 * wrapped in an IIFE and Babel-compiled, so top-level consts here can
 * never collide with the core bundle or another plugin. The page is
 * exported ONLY through the namespaced window registry at the bottom
 * (window.DomovoiPlugins.radio.pages.StationsPage) — never a bare
 * window global.
 *
 * Data sources (all under the plugin's own API prefix):
 *   * GET    /api/plugins/radio/stations?favorited_only=true   — favorites (paginated)
 *   * GET    /api/plugins/radio/recent                         — last 10 played
 *   * GET    /api/plugins/radio/badge                          — favorites total
 *   * GET    /api/plugins/radio/search?q=…                     — search proxy
 *   * POST   /api/plugins/radio/play                           — play, no favorite
 *   * POST   /api/plugins/radio/stations                       — favorite a hit
 *   * PATCH  /api/plugins/radio/stations/{id}                  — interval / unfavorite
 *   * DELETE /api/plugins/radio/stations/{id}                  — drop entirely
 *   * POST   /api/plugins/radio/fcc-import                     — async import job
 *   * /ws/state · `radio.stations.changed` / `radio.detections.changed`
 *
 * Playing is independent of favoriting: a row click anywhere on this page
 * starts the station in the browser player, and POST /play persists an
 * unsaved directory hit only so the stream proxy has an id to resolve.
 * The star is the only thing that favorites.
 *
 * Core-bundle globals used (loaded before any plugin script): React,
 * Card, Button, IconButton, Icon, Pill, StatusDot, Empty, PageHeader,
 * SleepingDomovoi, useToast, relTime, apiGet/apiPost/apiPatch/apiDelete,
 * useApiList, useApiObject, usePlayback.
 */

const RADIO_API = '/api/plugins/radio';
const ONLINE_TAG_PRESETS = ['indie', 'jazz', 'classical', 'news', 'electronic', 'rock'];
const PAGE_SIZE = 30;
/* Favorites are paginated server-side; the total comes from /badge (which
 * already counts favorites for the sidebar) rather than a second count
 * query. Recent is capped at 10 by the server's trim, not here. */
const FAVORITES_PAGE_SIZE = 20;

/* Can the BROWSER play this station's stream? The server's stream proxy
 * 409s on a missing/host-local URL (FM rows resolve to a transient local
 * address, and FCC imports have no URL at all until a simulcast is
 * resolved), so refuse those up front with a message that says what to do
 * instead of waiting for <audio> to fail. */
const browserPlayable = (st) => {
  const url = String(st?.stream_url || '').toLowerCase();
  if (!/^https?:\/\//.test(url)) return false;
  return !['localhost', '127.0.0.1', '://0.0.0.0'].some((h) => url.includes(h));
};

const unplayableReason = (st) => (
  st?.source === 'fm'
    ? `${st.name} has no online simulcast yet — resolve one, or play it through a room`
    : `${st?.name || 'that station'} has no browser-playable stream URL`
);

/* Build a player queue item for a station against the plugin's own
 * stream proxy route (the manifest's [[web.player_sources]] template). */
const radioQueueItem = (st) => ({
  uid: `radio-${st.id}-${Math.random().toString(36).slice(2, 7)}`,
  kind: 'radio',
  trackId: null,
  stationId: st.id,
  title: st.name || 'radio',
  artist: st.now_playing || (st.source === 'fm' ? 'FM' : 'online radio'),
  album: '',
  src: `${RADIO_API}/stations/${st.id}/stream`,
  coverUrl: null,
  durationSec: null,
  seekable: false,
  cacheable: false,
  meta: {},
});

/* ---- Search bar (online catalog + local FM browser) ------------- */
/*
 * Two scopes, one search surface:
 *   * "online"   → /search (station-directory catalog)
 *   * "local FM" → /stations?source=fm with q + frequency_mhz against
 *                  the FCC-imported rows.
 *
 * In online mode the input is a name/callsign substring; in local-FM
 * mode numeric input ("97.5") becomes an exact frequency match and
 * anything else matches name / call_sign / market_city.
 *
 * Pagination is "full-page-means-maybe-more" — no count query; Prev
 * disabled at offset 0, Next disabled when the page came back short.
 *
 * Favorites answer FIRST. Typing debounce-queries the local favorites
 * table (a cheap ILIKE against name/call sign/city) and renders the hits
 * above the directory results, which still only load on submit — the
 * station directory is a network hop and shouldn't be hit per keystroke.
 * Matching favorites are scope-independent: an FM favorite shows up while
 * the online scope is selected, because "where is that station I already
 * saved" is the question being answered.
 */
const StationSearch = ({ onFavorite, onPlay, fire }) => {
  const [scope, setScope] = React.useState('online');  // 'online' | 'fm'
  const [q, setQ] = React.useState('');
  const [country, setCountry] = React.useState('US');
  const [results, setResults] = React.useState([]);
  const [offset, setOffset] = React.useState(0);
  const [loading, setLoading] = React.useState(false);
  const [submitted, setSubmitted] = React.useState(false);
  const [favMatches, setFavMatches] = React.useState([]);

  // Reset paging + results when scope flips — stale results from the
  // previous scope confuse the column layout.
  React.useEffect(() => {
    setResults([]); setOffset(0); setSubmitted(false);
  }, [scope]);

  // Instant favorites. `alive` drops a response that lost the race to a
  // later keystroke, so the list can't flicker back to a stale query.
  React.useEffect(() => {
    const query = q.trim();
    if (!query) { setFavMatches([]); return; }
    let alive = true;
    const timer = setTimeout(async () => {
      try {
        const params = new URLSearchParams({
          favorited_only: 'true', q: query, limit: '8',
        });
        const out = await apiGet(`${RADIO_API}/stations?${params}`);
        if (alive) setFavMatches(Array.isArray(out) ? out : []);
      } catch {
        if (alive) setFavMatches([]);   // degrade quietly; directory still works
      }
    }, 220);
    return () => { alive = false; clearTimeout(timer); };
  }, [q]);

  const runSearch = async (override) => {
    const query = (override?.q ?? q).trim();
    const newOffset = override?.offset ?? 0;
    setLoading(true);
    try {
      let out;
      if (scope === 'online') {
        const cc = (override?.country ?? country).trim().toUpperCase();
        if (!query && !cc) {
          setResults([]); setSubmitted(false); return;
        }
        const params = new URLSearchParams();
        if (query) params.set('q', query);
        if (cc) params.set('country_code', cc);
        params.set('limit', String(PAGE_SIZE));
        params.set('offset', String(newOffset));
        out = await apiGet(`${RADIO_API}/search?${params}`);
      } else {
        const params = new URLSearchParams();
        params.set('source', 'fm');
        params.set('limit', String(PAGE_SIZE));
        params.set('offset', String(newOffset));
        if (query) {
          const asFreq = parseFloat(query);
          if (!Number.isNaN(asFreq) && /^\s*\d+(\.\d+)?\s*$/.test(query)) {
            params.set('frequency_mhz', String(asFreq));
          } else {
            params.set('q', query);
          }
        }
        out = await apiGet(`${RADIO_API}/stations?${params}`);
      }
      setResults(Array.isArray(out) ? out : []);
      setOffset(newOffset);
      setSubmitted(true);
    } catch (e) {
      fire(`search failed: ${e.message}`);
      setResults([]);
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = (e) => { e?.preventDefault?.(); runSearch({ offset: 0 }); };
  const goPrev = () => runSearch({ offset: Math.max(0, offset - PAGE_SIZE) });
  const goNext = () => runSearch({ offset: offset + PAGE_SIZE });

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const hasPrev = offset > 0;
  const hasNext = results.length === PAGE_SIZE;

  return (
    <Card>
      {/* Scope selector */}
      <div style={{ padding: '10px 16px', display: 'flex', gap: 6, alignItems: 'center',
                    borderBottom: '1px solid var(--border-soft)' }}>
        {[
          { id: 'online', label: 'online stations', sub: 'station directory' },
          { id: 'fm',     label: 'local FM',         sub: 'FCC-imported, near you' },
        ].map(s => (
          <button key={s.id} type="button" onClick={() => setScope(s.id)}
                  style={{ font: 'inherit', fontSize: 12, cursor: 'pointer',
                           padding: '6px 14px', borderRadius: 'var(--r-sm)',
                           border: '1px solid var(--border)',
                           background: scope === s.id ? 'var(--brand-soft)' : 'var(--card)',
                           color: scope === s.id ? 'var(--brand-press)' : 'var(--fg)',
                           fontWeight: scope === s.id ? 500 : 400 }}>
            {s.label}
            <span className="mono" style={{ marginLeft: 6, fontSize: 10, color: 'var(--fg-faint)' }}>
              {s.sub}
            </span>
          </button>
        ))}
      </div>

      <form onSubmit={onSubmit}
            style={{ padding: '12px 16px', display: 'flex', gap: 10, alignItems: 'center',
                     borderBottom: results.length || loading || submitted ? '1px solid var(--border-soft)' : 'none' }}>
        <div style={{ position: 'relative', flex: '1 1 320px' }}>
          <input value={q} onChange={e => setQ(e.target.value)}
                 placeholder={scope === 'online'
                   ? 'search stations — name, callsign, network…'
                   : 'name, call sign, city, or frequency (e.g. WJIM, Lansing, 97.5)'}
                 style={{ font: 'inherit', fontSize: 13, width: '100%', height: 32,
                          padding: '0 10px 0 30px', borderRadius: 'var(--r-sm)',
                          border: '1px solid var(--border)', background: 'var(--card)',
                          color: 'var(--fg)', boxShadow: 'var(--inner-highlight)' }}/>
          <span style={{ position: 'absolute', left: 9, top: 9, color: 'var(--fg-subtle)', pointerEvents: 'none' }}>
            <Icon name="search" size={14}/>
          </span>
        </div>
        {scope === 'online' && (
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--fg-muted)' }}>
            country
            <input value={country} onChange={e => setCountry(e.target.value)}
                   maxLength={2}
                   style={{ font: 'inherit', fontFamily: 'var(--ff-mono)', fontSize: 12, width: 48, height: 26,
                            padding: '0 8px', borderRadius: 'var(--r-sm)',
                            border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--fg)',
                            textTransform: 'uppercase' }}/>
          </label>
        )}
        <Button variant="primary" icon="search" type="submit" disabled={loading}>
          {loading ? 'searching…' : scope === 'fm' ? 'browse' : 'search'}
        </Button>
      </form>

      {/* Tag chip row — meaningful for the online directory only. */}
      {scope === 'online' && !loading && !submitted && (
        <div style={{ padding: '6px 16px 12px', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, color: 'var(--fg-faint)', alignSelf: 'center', marginRight: 4 }}>tags:</span>
          {ONLINE_TAG_PRESETS.map(t => (
            <button key={t} type="button"
                    onClick={() => { setQ(t); runSearch({ q: t, offset: 0 }); }}
                    style={{ font: 'inherit', fontSize: 11, cursor: 'pointer',
                             padding: '3px 9px', borderRadius: 'var(--r-full)',
                             border: '1px solid var(--border)', background: 'var(--card)',
                             color: 'var(--fg-muted)' }}>{t}</button>
          ))}
        </div>
      )}

      {/* Matching favorites — answered locally, so they're on screen
          before the directory request has even been sent. */}
      {favMatches.length > 0 && (
        <div style={{ borderBottom: '1px solid var(--border-soft)',
                      background: 'var(--sunken)' }}>
          <div style={{ padding: '8px 16px 2px', display: 'flex',
                        alignItems: 'center', gap: 6 }}>
            <Icon name="star" size={11}/>
            <div className="eyebrow">your favorites</div>
            <span className="mono" style={{ marginLeft: 'auto', fontSize: 10,
                                            color: 'var(--fg-faint)' }}>
              {favMatches.length} match{favMatches.length === 1 ? '' : 'es'}
            </span>
          </div>
          <div style={{ padding: '4px 8px 8px' }}>
            {favMatches.map((s) => (
              <FavoriteMatchRow key={`fav-${s.id}`} s={s} onPlay={onPlay}/>
            ))}
          </div>
        </div>
      )}

      {/* Local FM empty-state hint */}
      {scope === 'fm' && !loading && !submitted && (
        <div style={{ padding: '8px 16px 14px', fontSize: 11, color: 'var(--fg-muted)' }}>
          Tip: leave the box blank and hit <span className="mono">browse</span> to paginate the full FCC import. Type a number (<span className="mono">97.5</span>) to filter to that frequency, or a fragment (<span className="mono">WJIM</span>, <span className="mono">Lansing</span>) for a name / call-sign / city match.
        </div>
      )}

      {submitted && !loading && results.length === 0 && (
        <div style={{ padding: 32, textAlign: 'center' }}>
          <Empty glyph="sleeping" title="no stations match"
                 sub={scope === 'online'
                   ? (q ? `nothing in ${country || 'all countries'} called “${q}”` : `nothing for ${country}`)
                   : (q ? `no FCC FM rows match “${q}”` : 'no FCC FM rows imported yet — try Import FCC FM')}/>
        </div>
      )}

      {results.length > 0 && (
        <table className="tbl">
          <thead><tr>
            <th></th>
            {scope === 'online' ? (
              <>
                <th>name</th>
                <th>country</th>
                <th>tags</th>
              </>
            ) : (
              <>
                <th>call sign</th>
                <th>freq</th>
                <th>station</th>
                <th>market</th>
              </>
            )}
            <th className="actions"></th>
          </tr></thead>
          <tbody>
            {results.map(r => (
              <SearchResultRow key={(r.external_id || `id-${r.id}`) + '-' + offset}
                               hit={r} scope={scope}
                               onFavorite={onFavorite} onPlay={onPlay} fire={fire}/>
            ))}
          </tbody>
        </table>
      )}

      {submitted && (hasPrev || hasNext) && (
        <div style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 10,
                      borderTop: '1px solid var(--border-soft)', background: 'var(--sunken)' }}>
          <Button icon="chevron-left" onClick={goPrev} disabled={!hasPrev || loading}>prev</Button>
          <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
            page {page} · {results.length} shown
          </span>
          <span style={{ flex: 1 }}/>
          <Button icon="chevron-right" onClick={goNext} disabled={!hasNext || loading}>next</Button>
        </div>
      )}
    </Card>
  );
};

/* One matching favorite inside the search surface. Already saved, so
 * there's nothing to favorite here — the whole row just plays. */
const FavoriteMatchRow = ({ s, onPlay }) => (
  <button type="button" onClick={() => onPlay(s)}
          title={`play ${s.name}`}
          style={{ font: 'inherit', textAlign: 'left', cursor: 'pointer',
                   width: '100%', padding: '7px 8px', borderRadius: 'var(--r-sm)',
                   display: 'flex', alignItems: 'center', gap: 8,
                   background: 'transparent', border: 'none', color: 'var(--fg)' }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--card)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}>
    <Icon name={s.source === 'fm' ? 'radio-tower' : 'radio'} size={12}/>
    <span style={{ fontSize: 13, fontWeight: 500, minWidth: 0, overflow: 'hidden',
                   textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
      {s.name}
    </span>
    <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)',
                                    minWidth: 0, overflow: 'hidden',
                                    textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
      {s.source === 'fm' && s.frequency_mhz != null
        ? `${s.frequency_mhz} FM`
        : (s.call_sign || s.country_code || 'online')}
      {s.now_playing ? ` · ${s.now_playing}` : ''}
    </span>
    <span style={{ flex: 1 }}/>
    <Icon name="play" size={12} style={{ color: 'var(--brand)', flexShrink: 0 }}/>
  </button>
);

const SearchResultRow = ({ hit, scope, onFavorite, onPlay, fire }) => {
  // Local favorited state so the star feels snappy; the realtime push
  // refreshes the canonical list.
  const [favorited, setFavorited] = React.useState(hit.favorited);
  const [busy, setBusy] = React.useState(false);

  const toggle = async () => {
    if (busy) return;
    setBusy(true);
    try {
      // Local-FM rows always have a real DB id (they came from
      // /stations); online-search hits carry id=0 until POSTed.
      if (hit.id && hit.id !== 0) {
        const newFav = !favorited;
        await apiPatch(`${RADIO_API}/stations/${hit.id}`, { favorited: newFav });
        setFavorited(newFav);
        fire(newFav ? `favorited ${hit.name}` : `unfavorited ${hit.name}`);
        // FM rows from the FCC import have no stream_url; right after
        // favoriting, fire the simulcast resolver so the poller has
        // something to hit. Kept separate from the PATCH so a slow /
        // failing directory lookup can't bounce the favorite itself.
        if (newFav && hit.source === 'fm' && !hit.stream_url) {
          try {
            const res = await apiPost(
              `${RADIO_API}/stations/${hit.id}/resolve-simulcast`, {},
            );
            if (res?.resolved) {
              fire(`simulcast found for ${hit.name}`);
            } else if (res?.message) {
              fire(`no simulcast: ${res.message}`);
            }
          } catch (e) {
            fire(`simulcast lookup failed: ${e.message}`);
          }
        }
      } else if (!favorited) {
        await onFavorite(hit);
        setFavorited(true);
      }
    } catch (e) {
      fire(`favorite failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  // The row plays; only the star cell favorites. Keeping those separate is
  // the whole point — a station you just want to hear shouldn't end up in
  // the favorites list (and on the sampler's poll schedule) to be heard.
  const playable = browserPlayable(hit);
  const rowProps = {
    onClick: () => onPlay(hit),
    title: playable ? `play ${hit.name}` : unplayableReason(hit),
    style: { cursor: 'pointer' },
  };
  return (
    <tr {...rowProps}>
      <td onClick={e => e.stopPropagation()} style={{ width: 40 }}>
        <IconButton name="star" onClick={toggle}
                    title={favorited ? 'unfavorite' : 'favorite'}
                    style={favorited ? { color: 'var(--brand)' } : undefined}/>
      </td>
      {scope === 'online' ? (
        <>
          <td>
            <div style={{ fontWeight: 500 }}>{hit.name}</div>
            {hit.stream_url && (
              <div className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)',
                                              overflow: 'hidden', textOverflow: 'ellipsis',
                                              whiteSpace: 'nowrap', maxWidth: 280 }}>
                {hit.stream_url}
              </div>
            )}
          </td>
          <td className="mono" style={{ fontSize: 11 }}>{hit.country_code || '—'}</td>
          <td>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {(hit.tags || []).slice(0, 3).map(t => (
                <span key={t} style={{ fontSize: 10, padding: '2px 6px', borderRadius: 'var(--r-full)',
                                        background: 'var(--sunken)', color: 'var(--fg-muted)' }}>{t}</span>
              ))}
            </div>
          </td>
        </>
      ) : (
        <>
          <td className="mono" style={{ fontSize: 12, fontWeight: 500 }}>
            {hit.call_sign || '—'}
          </td>
          <td className="mono" style={{ fontSize: 12 }}>
            {hit.frequency_mhz != null ? `${hit.frequency_mhz} FM` : '—'}
          </td>
          <td style={{ fontSize: 13 }}>{hit.name || '—'}</td>
          <td className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
            {[hit.market_city, hit.market_state].filter(Boolean).join(', ') || '—'}
          </td>
        </>
      )}
      <td className="actions" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6,
                      justifyContent: 'flex-end' }}>
          {favorited && <Pill tone="live">saved</Pill>}
          <Button icon="play" onClick={() => onPlay(hit)} disabled={!playable}
                  title={playable ? `play ${hit.name}` : unplayableReason(hit)}>
            play
          </Button>
        </div>
      </td>
    </tr>
  );
};

/* ---- Favorited stations list ----------------------------------- */
const FavoritesList = ({ stations, loading, page, selectedId, onSelect, onPlay, onDelete, fire, refresh }) => {
  if (loading && stations.length === 0)
    return <div style={{ padding: 24, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading favorites…</div>;
  if (stations.length === 0)
    return page > 0
      ? <div style={{ padding: 24, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>nothing on this page</div>
      : <Empty glyph="sleeping" title="no favorites yet" sub="search and tap the star to start collecting"/>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {stations.map(s => (
        <FavoriteRow key={s.id} s={s}
                     active={selectedId === s.id}
                     onSelect={() => onSelect(s.id)}
                     onPlay={onPlay}
                     onDelete={onDelete}
                     refresh={refresh}
                     fire={fire}/>
      ))}
    </div>
  );
};

/* ---- Recent strip (the last 10 stations actually played) -------- */
/*
 * Server-trimmed to 10 rows — there is no longer history to page through,
 * by design. A row here can be a favorite or a station played once out of
 * search; the star tells them apart and promotes the latter.
 */
const RecentList = ({ stations, loading, onPlay, onFavorite, fire }) => {
  if (loading && stations.length === 0)
    return <div style={{ padding: 20, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading…</div>;
  if (stations.length === 0)
    return (
      <div style={{ padding: '16px 14px', fontSize: 12, color: 'var(--fg-muted)' }}>
        Nothing played yet — click any station to start it.
      </div>
    );
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {stations.map(s => <RecentRow key={s.id} s={s} onPlay={onPlay}
                                    onFavorite={onFavorite} fire={fire}/>)}
    </div>
  );
};

const RecentRow = ({ s, onPlay, onFavorite, fire }) => {
  const [busy, setBusy] = React.useState(false);
  // Recent rows are already persisted, so favoriting is a PATCH either
  // way — no POST /stations path to worry about here.
  const toggleFavorite = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await apiPatch(`${RADIO_API}/stations/${s.id}`, { favorited: !s.favorited });
      fire(s.favorited ? `unfavorited ${s.name}` : `favorited ${s.name}`);
      onFavorite && onFavorite();
    } catch (e) {
      fire(`favorite failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ borderTop: '1px solid var(--border-soft)', display: 'flex',
                  alignItems: 'center' }}>
      <button onClick={() => onPlay(s)} title={`play ${s.name}`}
              style={{ font: 'inherit', textAlign: 'left', cursor: 'pointer',
                       flex: 1, minWidth: 0, padding: '9px 4px 9px 14px',
                       display: 'flex', alignItems: 'center', gap: 7,
                       background: 'transparent', border: 'none', color: 'var(--fg)' }}>
        <Icon name={s.source === 'fm' ? 'radio-tower' : 'radio'} size={12}/>
        <span style={{ fontSize: 13, minWidth: 0, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {s.name}
        </span>
        {s.last_played_at && (
          <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)',
                                          flexShrink: 0 }}>
            {relTime(s.last_played_at)}
          </span>
        )}
      </button>
      <div style={{ paddingRight: 8, flexShrink: 0 }}>
        <IconButton name="star" onClick={toggleFavorite}
                    title={s.favorited ? 'unfavorite' : 'favorite'}
                    style={s.favorited ? { color: 'var(--brand)' } : undefined}/>
      </div>
    </div>
  );
};

/* ---- Now-playing line for a favorited station ------------------ */
/*
 * Driven by the ICY poller, which caches StreamTitle onto
 * `radio_stations.now_playing` + `now_playing_updated_at`. The
 * tristate `icy_supported` distinguishes "haven't probed yet" (null),
 * "ICY confirmed" (true), and "no ICY headers after several polls"
 * (false) — the latter shown quietly so the user knows the audio
 * sampler is that station's only detection source.
 */
const NowPlayingLine = ({ s }) => {
  const np = s.now_playing;
  if (np) {
    return (
      <div style={{ fontSize: 11, color: 'var(--fg)', marginTop: 2,
                    overflow: 'hidden', textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap', display: 'flex',
                    alignItems: 'center', gap: 4 }}>
        <Icon name="music" size={10}/>
        <span style={{ minWidth: 0, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {np}
        </span>
        {s.now_playing_updated_at && (
          <span className="mono" style={{ fontSize: 10, color: 'var(--fg-faint)',
                                          flexShrink: 0 }}>
            · {relTime(s.now_playing_updated_at)}
          </span>
        )}
      </div>
    );
  }
  if (s.icy_supported === false) {
    return (
      <div style={{ fontSize: 11, color: 'var(--fg-faint)', fontStyle: 'italic',
                    marginTop: 2 }}>
        no live metadata
      </div>
    );
  }
  // icy_supported null (never probed) or true-with-no-title-yet.
  return (
    <div style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>
      listening…
    </div>
  );
};

const FavoriteRow = ({ s, active, onSelect, onPlay, onDelete, refresh, fire }) => {
  const [editing, setEditing] = React.useState(false);
  const [intervalDraft, setIntervalDraft] = React.useState(s.sample_interval_sec);
  React.useEffect(() => { setIntervalDraft(s.sample_interval_sec); setEditing(false); }, [s.id, s.sample_interval_sec]);

  const saveInterval = async () => {
    const v = parseInt(intervalDraft, 10);
    if (!Number.isFinite(v) || v < 30) { fire('interval must be ≥ 30 s'); return; }
    try {
      await apiPatch(`${RADIO_API}/stations/${s.id}`, { sample_interval_sec: v });
      fire(`${s.name}: sampling every ${v}s`);
      setEditing(false);
      refresh();
    } catch (e) {
      fire(`save failed: ${e.message}`);
    }
  };

  return (
    <div style={{ borderTop: '1px solid var(--border-soft)',
                  background: active ? 'var(--brand-soft)' : 'transparent',
                  borderLeftWidth: 3, borderLeftStyle: 'solid',
                  borderLeftColor: active ? 'var(--brand)' : 'transparent' }}>
      {/* The row PLAYS. Details (the detection feed + stream URL editor) live
          behind the chevron, so the common action — "put this on" — is one
          click and the uncommon one is still one click. The chevron is a
          SIBLING of the play button, not nested inside it: a button inside a
          button is invalid markup, and relying on stopPropagation to tell the
          two apart is a trap the first keyboard user would find. */}
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <button onClick={() => onPlay(s)} title={`play ${s.name}`}
                style={{ font: 'inherit', textAlign: 'left', cursor: 'pointer',
                         flex: 1, minWidth: 0, padding: '12px 6px 12px 14px',
                         display: 'grid', gridTemplateColumns: '1fr auto', gap: 8,
                         alignItems: 'center',
                         background: 'transparent', border: 'none',
                         color: active ? 'var(--brand-press)' : 'var(--fg)' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Icon name={s.source === 'fm' ? 'radio-tower' : 'radio'} size={12}/>
              <span style={{ fontSize: 13, fontWeight: 500 }}>{s.name}</span>
            </div>
            <NowPlayingLine s={s}/>
            <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
              {s.source === 'fm' && s.frequency_mhz != null
                ? `${s.frequency_mhz} FM`
                : s.country_code || 'online'}
              {' · sampling every '}{s.sample_interval_sec}s
            </div>
          </div>
          <StatusDot tone={s.last_sampled_at ? 'ok' : 'idle'} live={!!s.last_sampled_at}/>
        </button>
        <div style={{ paddingRight: 6, flexShrink: 0 }}>
          <IconButton name={active ? 'chevron-down' : 'chevron-right'}
                      onClick={onSelect}
                      title={active ? 'hide details' : 'details & detections'}/>
        </div>
      </div>

      {active && (
        <div style={{ padding: '0 14px 12px 14px', display: 'flex', alignItems: 'center', gap: 8 }}>
          {editing ? (
            <>
              <input type="number" min={30} max={86400} value={intervalDraft}
                     onChange={e => setIntervalDraft(e.target.value)}
                     onKeyDown={e => { if (e.key === 'Enter') saveInterval(); if (e.key === 'Escape') setEditing(false); }}
                     style={{ font: 'inherit', fontFamily: 'var(--ff-mono)', fontSize: 12, width: 76, height: 26,
                              padding: '0 8px', borderRadius: 'var(--r-sm)',
                              border: '1px solid var(--brand)', background: 'var(--card)', color: 'var(--fg)' }}/>
              <span style={{ fontSize: 11, color: 'var(--fg-muted)' }}>seconds</span>
              <Button icon="check" variant="primary" onClick={saveInterval}>save</Button>
              <Button icon="x" onClick={() => setEditing(false)}>cancel</Button>
            </>
          ) : (
            <>
              <Button icon="headphones" onClick={() => onPlay(s)}>play here</Button>
              <Button icon="pencil" onClick={() => setEditing(true)}>interval</Button>
              <span style={{ flex: 1 }}/>
              <Button icon="trash-2" onClick={() => onDelete(s)}
                      style={{ background: 'var(--err-soft)', color: 'var(--err)', borderColor: 'transparent' }}>
                forget
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  );
};

/* ---- Detection feed (one detection per row) -------------------- */
const DetectionRow = ({ d }) => {
  // Three sources:
  //   * local  — local fingerprint hit (highest trust; we already own
  //     the audio that matched). Pill = `live`.
  //   * icy    — StreamTitle from the station's metadata channel: free
  //     and fast but station-author-supplied.
  //   * shazam — online audio-fingerprint hit. Solid but expensive.
  const sourceTone = d.fingerprint_source === 'local' ? 'live' : 'idle';
  const sourceLabel = (
    d.fingerprint_source === 'local' ? 'local'
    : d.fingerprint_source === 'icy' ? 'icy'
    : 'shazam'
  );
  return (
    <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border-soft)',
                  display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 10, alignItems: 'center' }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {d.title || '—'}
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
          {d.artist || 'unknown artist'} · {relTime(d.detected_at)}
        </div>
      </div>
      <Pill tone={sourceTone}>{sourceLabel}</Pill>
      {d.in_library
        ? <Pill tone="ok">in library</Pill>
        : <Pill tone="idle">new</Pill>}
    </div>
  );
};

const DetectionFeed = ({ stationId }) => {
  const { items: detections, loading } = useApiList(
    `${RADIO_API}/detections?station_id=${stationId}&limit=100`,
    { eventTypes: ['radio.detections.changed'] },
  );

  if (loading && detections.length === 0)
    return <div style={{ padding: 28, textAlign: 'center', fontSize: 12, color: 'var(--fg-muted)' }}>loading detections…</div>;
  if (detections.length === 0)
    return <Empty glyph="sleeping" title="no detections yet"
                  sub="the detectors run every few minutes — give it a song or two"/>;
  return (
    <div>
      {detections.map(d => <DetectionRow key={d.id} d={d}/>)}
    </div>
  );
};

/* ---- Stream URL editor (StationDetail's "stream" row) ---------- */
const StreamUrlEditor = ({ s, fire }) => {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(s.stream_url || '');
  const [resolving, setResolving] = React.useState(false);

  React.useEffect(() => {
    setDraft(s.stream_url || '');
    setEditing(false);
  }, [s.id, s.stream_url]);

  const save = async () => {
    const v = draft.trim();
    if (v && !/^https?:\/\//i.test(v)) {
      fire('stream URL must start with http(s)://');
      return;
    }
    try {
      await apiPatch(`${RADIO_API}/stations/${s.id}`, { stream_url: v || null });
      fire(v ? 'stream URL saved' : 'stream URL cleared');
      setEditing(false);
    } catch (e) {
      fire(`save failed: ${e.message}`);
    }
  };

  const resolve = async () => {
    if (resolving) return;
    setResolving(true);
    try {
      const res = await apiPost(`${RADIO_API}/stations/${s.id}/resolve-simulcast`, {});
      if (res?.resolved) {
        fire(`found simulcast for ${s.call_sign || s.name}`);
      } else {
        fire(res?.message || 'no simulcast found');
      }
    } catch (e) {
      fire(`resolve failed: ${e.message}`);
    } finally {
      setResolving(false);
    }
  };

  if (editing) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <input value={draft} onChange={e => setDraft(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false); }}
               placeholder="http(s)://…"
               style={{ font: 'inherit', fontFamily: 'var(--ff-mono)', fontSize: 11,
                        flex: 1, minWidth: 0, height: 26, padding: '0 8px',
                        borderRadius: 'var(--r-sm)', border: '1px solid var(--brand)',
                        background: 'var(--card)', color: 'var(--fg)' }}/>
        <Button icon="check" variant="primary" onClick={save}>save</Button>
        <Button icon="x" onClick={() => { setDraft(s.stream_url || ''); setEditing(false); }}>cancel</Button>
      </div>
    );
  }

  if (s.stream_url) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)',
                                         wordBreak: 'break-all', flex: 1, minWidth: 0 }}>
          {s.stream_url}
        </span>
        <button type="button" onClick={() => setEditing(true)}
                title="edit stream URL"
                style={{ font: 'inherit', cursor: 'pointer', background: 'transparent',
                         border: 'none', padding: 4, color: 'var(--fg-subtle)',
                         display: 'inline-flex', alignItems: 'center' }}>
          <Icon name="pencil" size={12}/>
        </button>
      </div>
    );
  }

  // Empty state. FM rows can auto-resolve; everything else pastes.
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 11, color: 'var(--fg-faint)', fontStyle: 'italic' }}>
        no stream URL — polling can't reach this station
      </span>
      <span style={{ flex: 1 }}/>
      {s.source === 'fm' && (
        <Button icon="search" onClick={resolve} disabled={resolving}>
          {resolving ? 'resolving…' : 'resolve'}
        </Button>
      )}
      <Button icon="pencil" onClick={() => setEditing(true)}>paste URL</Button>
    </div>
  );
};

/* ---- Detail pane (overview + detection feed) ------------------- */
const StationDetail = ({ s, fire }) => (
  <Card>
    <div style={{ padding: '20px 16px', borderBottom: '1px solid var(--border-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Icon name={s.source === 'fm' ? 'radio-tower' : 'radio'} size={18}/>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, letterSpacing: '-0.005em' }}>{s.name}</h2>
        {s.last_sampled_at && (
          <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-muted)' }}>
            last sampled {relTime(s.last_sampled_at)}
          </span>
        )}
      </div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 4 }}>
        station · #{s.id} · {s.source}{s.frequency_mhz != null ? ` · ${s.frequency_mhz} mhz` : ''}
      </div>
    </div>
    {/* Freshest signal first: the ICY now-playing callout. */}
    {s.now_playing && (
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-soft)',
                    background: 'var(--brand-soft)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name="music" size={14}/>
        <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--brand-press)',
                       minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis',
                       whiteSpace: 'nowrap', flex: 1 }}>
          {s.now_playing}
        </span>
        {s.now_playing_updated_at && (
          <span className="mono" style={{ fontSize: 11, color: 'var(--brand-press)', opacity: 0.7 }}>
            {relTime(s.now_playing_updated_at)}
          </span>
        )}
      </div>
    )}
    <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: '110px 1fr', rowGap: 8,
                  fontSize: 12, borderBottom: '1px solid var(--border-soft)' }}>
      <div className="label">stream</div>
      <div><StreamUrlEditor s={s} fire={fire}/></div>
      <div className="label">country</div>
      <div className="mono">{s.country_code || '—'}</div>
      <div className="label">language</div>
      <div className="mono">{s.language || '—'}</div>
      <div className="label">tags</div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {(s.tags || []).map(t => (
          <span key={t} style={{ fontSize: 10, padding: '2px 6px', borderRadius: 'var(--r-full)',
                                  background: 'var(--sunken)', color: 'var(--fg-muted)' }}>{t}</span>
        ))}
        {(!s.tags || s.tags.length === 0) && <span style={{ color: 'var(--fg-faint)' }}>—</span>}
      </div>
      <div className="label">interval</div>
      <div className="mono">{s.sample_interval_sec}s</div>
    </div>
    <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border-soft)',
                  background: 'var(--sunken)' }}>
      <div className="eyebrow">recent detections</div>
    </div>
    <DetectionFeed stationId={s.id}/>
  </Card>
);

/* ---- FCC import button (async job — POST then poll) ------------- */
const FccImportButton = ({ fire }) => {
  const [running, setRunning] = React.useState(false);

  const poll = async (attempts) => {
    for (let i = 0; i < attempts; i++) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const st = await apiGet(`${RADIO_API}/fcc-import`);
        if (st?.state === 'done') {
          const r = st.result || {};
          if (!r.state) {
            fire('fcc import: no market state configured (set RADIO_MARKET_STATE)');
          } else {
            fire(`fcc ${r.state}: ${r.inserted} new, ${r.updated} updated`);
          }
          return;
        }
        if (st?.state === 'failed') {
          fire(`fcc import failed: ${st.error || 'unknown error'}`);
          return;
        }
      } catch (e) {
        fire(`fcc status check failed: ${e.message}`);
        return;
      }
    }
    fire('fcc import still running — it will finish in the background');
  };

  const start = async () => {
    if (running) return;
    setRunning(true);
    fire('fcc import started…');
    try {
      // The import runs as a background job on the core — this POST
      // returns immediately and we poll for the outcome.
      await apiPost(`${RADIO_API}/fcc-import`, {});
      await poll(45);
    } catch (e) {
      fire(`fcc import failed: ${e.message}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <Button icon="download" onClick={start} disabled={running}>
      {running ? 'importing…' : 'Import FCC FM'}
    </Button>
  );
};

/* ---- Page ------------------------------------------------------- */
const StationsPage = () => {
  const [selectedId, setSelectedId] = React.useState(null);
  const [favPage, setFavPage] = React.useState(0);
  const [fire, toastNode] = useToast();

  // Favorites are paged server-side. The total comes from /badge (the
  // sidebar's favorites count) so there's no second count query — and it
  // refetches on the same realtime event, so starring something on the
  // phone renumbers the pages here.
  const { items: favorites, loading, refresh } = useApiList(
    `${RADIO_API}/stations?favorited_only=true&limit=${FAVORITES_PAGE_SIZE}`
    + `&offset=${favPage * FAVORITES_PAGE_SIZE}`,
    { eventTypes: ['radio.stations.changed'] },
  );
  const { data: badge } = useApiObject(`${RADIO_API}/badge`,
                                       { eventTypes: ['radio.stations.changed'] });
  const { items: recent, loading: recentLoading, refresh: refreshRecent } =
    useApiList(`${RADIO_API}/recent`, { eventTypes: ['radio.stations.changed'] });

  const favTotal = badge && typeof badge.favorites === 'number' ? badge.favorites : null;
  const favPages = favTotal != null ? Math.max(1, Math.ceil(favTotal / FAVORITES_PAGE_SIZE)) : null;
  const hasPrevFav = favPage > 0;
  // With a known total, trust it; otherwise fall back to the page-is-full
  // heuristic the search surface uses.
  const hasNextFav = favTotal != null
    ? (favPage + 1) * FAVORITES_PAGE_SIZE < favTotal
    : favorites.length === FAVORITES_PAGE_SIZE;

  // Unfavoriting the last row on the last page would otherwise strand the
  // user on an empty page they can only escape with Prev.
  React.useEffect(() => {
    if (!loading && favorites.length === 0 && favPage > 0) setFavPage(p => p - 1);
  }, [loading, favorites.length, favPage]);

  const selected = favorites.find(s => s.id === selectedId) || null;

  // The one playback path on this page. Favoriting is NOT implied: POST
  // /play persists an unsaved directory hit only because the stream proxy
  // resolves row ids, and marks it created_by_play so the server's Recent
  // trim reclaims it if it never gets starred.
  const player = usePlayback();
  const playStation = async (st) => {
    if (!player.available) { fire('browser player not available'); return; }
    if (!browserPlayable(st)) { fire(unplayableReason(st)); return; }
    try {
      const row = await apiPost(`${RADIO_API}/play`, st.id
        ? { station_id: st.id }
        : {
            name: st.name,
            source: st.source || 'online',
            stream_url: st.stream_url,
            external_id: st.external_id,
            country_code: st.country_code,
            language: st.language,
            tags: st.tags || [],
          });
      player.playItems([radioQueueItem(row)]);
      fire(`playing ${row.name}`);
      // The realtime event refetches both lists within a tick or two; this
      // makes the Recent strip move under the click that caused it.
      refreshRecent();
    } catch (e) {
      fire(`play failed: ${e.message}`);
    }
  };

  const onFavorite = async (hit) => {
    // POST the search-hit shape — the server is idempotent on external_id.
    await apiPost(`${RADIO_API}/stations`, {
      name: hit.name,
      source: hit.source || 'online',
      stream_url: hit.stream_url,
      external_id: hit.external_id,
      country_code: hit.country_code,
      language: hit.language,
      tags: hit.tags || [],
    });
    fire(`favorited ${hit.name}`);
    refresh();
  };

  const onDelete = async (s) => {
    try {
      await apiDelete(`${RADIO_API}/stations/${s.id}`);
      fire(`forgot ${s.name}`);
      if (selectedId === s.id) setSelectedId(null);
      refresh();
      refreshRecent();
    } catch (e) {
      fire(`forget failed: ${e.message}`);
    }
  };

  return (
    <div className="page">
      <PageHeader
        title="Stations"
        sub={`${favTotal != null ? favTotal : favorites.length} favorited`
             + ` · ${recent.length} recent · click any station to play it`}
        actions={<FccImportButton fire={fire}/>}
      />

      {/* [1] Search */}
      <StationSearch onFavorite={onFavorite} onPlay={playStation} fire={fire}/>

      {/* [2] Recent + favorites + detail */}
      <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 16, alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border-soft)',
                          display: 'flex', alignItems: 'center', gap: 8 }}>
              <Icon name="history" size={13}/>
              <div style={{ fontSize: 13, fontWeight: 500 }}>Recent</div>
              <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-faint)' }}>
                last {recent.length || 0}
              </span>
            </div>
            <RecentList stations={recent} loading={recentLoading}
                        onPlay={playStation}
                        onFavorite={() => { refresh(); refreshRecent(); }}
                        fire={fire}/>
          </Card>

          <Card>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border-soft)',
                          display: 'flex', alignItems: 'center', gap: 8 }}>
              <Icon name="star" size={13}/>
              <div style={{ fontSize: 13, fontWeight: 500 }}>Favorites</div>
              <span className="mono" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-faint)' }}>
                {favTotal != null ? favTotal : favorites.length}
              </span>
            </div>
            <FavoritesList stations={favorites} loading={loading} page={favPage}
                           selectedId={selectedId} onSelect={setSelectedId}
                           onPlay={playStation}
                           onDelete={onDelete} refresh={refresh} fire={fire}/>
            {(hasPrevFav || hasNextFav) && (
              <div style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 8,
                            borderTop: '1px solid var(--border-soft)', background: 'var(--sunken)' }}>
                <Button icon="chevron-left" onClick={() => setFavPage(p => Math.max(0, p - 1))}
                        disabled={!hasPrevFav || loading}>prev</Button>
                <span className="mono" style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                  {favPages != null ? `page ${favPage + 1} / ${favPages}` : `page ${favPage + 1}`}
                </span>
                <span style={{ flex: 1 }}/>
                <Button icon="chevron-right" onClick={() => setFavPage(p => p + 1)}
                        disabled={!hasNextFav || loading}>next</Button>
              </div>
            )}
          </Card>
        </div>

        {selected ? (
          <StationDetail s={selected} fire={fire}/>
        ) : (
          <Card>
            <div style={{ padding: '64px 24px', textAlign: 'center' }}>
              <div style={{ display: 'inline-block', color: 'var(--fg-subtle)', marginBottom: 12 }}>
                <SleepingDomovoi size={120}/>
              </div>
              <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg)' }}>Click a station to play it</div>
              <div style={{ fontSize: 13, color: 'var(--fg-muted)', marginTop: 6, maxWidth: 360, margin: '6px auto 0' }}>
                Playing doesn’t favorite — the star does that. Open a favorite’s
                chevron for its stream settings and the detections the detectors
                have heard on it.
              </div>
            </div>
          </Card>
        )}
      </div>

      {toastNode}
    </div>
  );
};

/* Namespaced window registry (design §5.2) — the only global export. */
window.DomovoiPlugins = window.DomovoiPlugins || {};
window.DomovoiPlugins.radio = window.DomovoiPlugins.radio || {};
window.DomovoiPlugins.radio.pages = Object.assign(
  {}, window.DomovoiPlugins.radio.pages, { StationsPage },
);
