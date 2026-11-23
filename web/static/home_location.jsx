/* Settings → Household → Home location (owner decision, 2026-11-22).
 *
 * The household's place: weather answers and local news use it, and
 * plugins read it (sdk.household.home_location()). One field with
 * type-ahead from the town list bundled with Domovoi (GeoNames,
 * CC BY 4.0, credited under the field); typing never leaves the house.
 * Until 1.4.1 it was "Local news location" in Settings → System →
 * Advanced → News; the first 1.4.1 boot carried that text over, and a
 * name no town matched asks to be picked from the list.
 *
 * Data:
 *   GET /api/household/home-location        · { place, needs_pick, carried_over, attribution }
 *   PUT /api/household/home-location        · { name, lat, lon, country } (admin); { name: '' } clears
 *   GET /api/places/suggest?q=&limit=8      · { places: [{ name, lat, lon, country }] }
 */

const HomeLocationPanel = () => {
  const admin = useAdminSignedIn();
  const [fire, toastNode] = useToast();
  const { data, refresh } = useApiObject('/api/household/home-location', { quiet: true });
  const [text, setText] = React.useState('');
  const [hits, setHits] = React.useState([]);
  const [at, setAt] = React.useState(0);
  const [saving, setSaving] = React.useState(false);
  const asked = React.useRef(0);
  const place = (data && data.place) || null;

  React.useEffect(() => { setText(place ? place.name : ''); }, [place && place.name]);

  const look = (q) => {
    const n = ++asked.current;
    if (!q.trim()) { setHits([]); return; }
    apiGet(`/api/places/suggest?q=${encodeURIComponent(q.trim())}&limit=8`, { quiet: true })
      .then((r) => { if (n === asked.current) { setHits((r && r.places) || []); setAt(0); } })
      .catch(() => { if (n === asked.current) setHits([]); });
  };

  const save = async (body, said) => {
    setSaving(true);
    try {
      await apiPut('/api/household/home-location', body);
      setHits([]);
      fire(said, { tone: 'ok' });
      refresh();
    } catch (e) {
      fire(`not saved: ${(e && e.message) || 'the server said no'}`, { tone: 'warn' });
    } finally { setSaving(false); }
  };
  const pick = (p) => p && save(p, `home location: ${p.name}`);

  const onKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setAt((n) => Math.min(n + 1, Math.max(hits.length - 1, 0))); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setAt((n) => Math.max(n - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (hits.length) pick(hits[Math.min(at, hits.length - 1)]); }
    else if (e.key === 'Escape') { setHits([]); }
  };

  if (!data) return <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>loading…</div>;
  return (
    <div className="home-location" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {toastNode}
      <Card title="Home location"
            sub="The town Domovoi’s weather answers and local news are for. Plugins can read it too.">
        <div data-settings-mark="home-location" style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {data.needs_pick && (
            <div className="chat-note info" role="note">
              {data.carried_over ? 'Carried over from the old news location: ' : ''}
              “{place.name}” isn’t a town on the list yet. Pick your town from the list so weather and news find the right place.
            </div>
          )}
          <div style={{ position: 'relative' }}>
            <input type="search" className="cal-inp" value={text} disabled={!admin || saving}
                   placeholder="start typing your town · Denver…" aria-label="home location"
                   autoComplete="off" role="combobox" aria-expanded={hits.length > 0}
                   aria-controls="home-location-list" style={{ width: '100%' }}
                   onChange={(e) => { setText(e.target.value); look(e.target.value); }}
                   onKeyDown={onKey}/>
            {hits.length > 0 && (
              <div id="home-location-list" className="settings-search-results" role="listbox">
                {hits.map((p, i) => (
                  <a key={`${p.name}/${p.lat}/${p.lon}`} href="#" role="option" aria-selected={i === at}
                     className={`settings-search-hit${i === at ? ' on' : ''}`}
                     onClick={(e) => { e.preventDefault(); pick(p); }}>
                    <span className="settings-search-label">{p.name}</span>
                  </a>
                ))}
              </div>
            )}
          </div>
          <div style={{ fontSize: 12, color: 'var(--fg-muted)', display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            {admin
              ? (place ? <span>Now: <strong>{place.name}</strong></span> : <span>Not set: weather answers ask you to set it.</span>)
              : <span>An admin can change this.</span>}
            {admin && place && (
              <button type="button" className="home-link" disabled={saving}
                      onClick={() => save({ name: '' }, 'home location cleared')}>clear</button>
            )}
          </div>
          <div style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
            The town list comes with Domovoi; typing here never leaves your house.{' '}
            Town list from <a href="https://www.geonames.org/" target="_blank" rel="noopener noreferrer">GeoNames</a>,{' '}
            <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">CC BY 4.0</a>.
          </div>
        </div>
      </Card>
    </div>
  );
};

window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections['home-location'] = { Panel: HomeLocationPanel };
