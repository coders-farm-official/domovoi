/* Settings → Household → Music mix (owner, 2026-11-14).
 *
 * How Up next chooses songs, in plain controls instead of weights
 * (domovoi/up_next/mix.py):
 *
 *   • Discovery ↔ Favorites (five steps; the middle is how Up next always
 *     chose): Discovery leans harder toward songs not heard in a while,
 *     Favorites toward what the house plays most.
 *   • Stay close ↔ Wander (five steps): how closely songs follow the one
 *     playing (artist, era, genre, playlists) versus wildcards from
 *     anywhere in the library.
 *   • Autoplay when a playlist ends: the default a new Up next takes.
 *   • No repeats within N songs: a list shorter than N plays once, then
 *     similar songs follow; a longer one starts over reshuffled.
 *   • Similar artists / song popularity from the internet: the two
 *     ListenBrainz switches (admin, core settings), shown greyed with the
 *     reason when they can't be changed from here.
 *
 * Each person can set their own two sliders (PersonMusicMix, drawn in the
 * People page's Profile tab).
 *
 * Data:
 *   GET/PUT /api/music/mix                       · the household's mix
 *   GET/PUT /api/people/{id}/music-mix           · a person's own sliders
 *   GET /api/config/editable, PATCH …            · the internet switches (admin)
 */

const MIX_DISCOVERY = {
  left: 'Discovery', right: 'Favorites',
  words: {
    1: 'mostly songs you haven’t heard in a while',
    2: 'more songs you haven’t heard in a while',
    3: 'a balance (the default)',
    4: 'more of what you play most',
    5: 'mostly what you play most',
  },
};
const MIX_WANDER = {
  left: 'Stay close', right: 'Wander',
  words: {
    1: 'stays close to the song playing',
    2: 'mostly close to the song playing',
    3: 'a balance (the default)',
    4: 'wanders more across your library',
    5: 'wanders widely across your library',
  },
};

/* A five-step slider with its two ends named and the step said in words. */
const MixSlider = ({ id, spec, value, disabled, onChange }) => {
  const v = typeof value === 'number' ? value : 3;
  return (
    <div className="config-slider mix-slider">
      <div className="config-slider-track">
        <input type="range" min={1} max={5} step={1} value={v} disabled={disabled}
               aria-label={`${spec.left} or ${spec.right}`} aria-valuetext={spec.words[v]}
               aria-describedby={`${id}-ends`}
               onChange={(e) => onChange(Number(e.target.value))}/>
        <div className="config-slider-ends" id={`${id}-ends`}>
          <span>{spec.left}</span>
          <span>{spec.right}</span>
        </div>
      </div>
      <div className="mix-slider-now" aria-hidden="true">{spec.words[v]}</div>
    </div>
  );
};

const MusicMixPanel = () => {
  const admin = useAdminSignedIn();
  const [fire, toastNode] = useToast();
  const { data, refresh } = useApiObject('/api/music/mix', { quiet: true });
  const { data: cfg, refresh: refreshCfg } = useApiObject(admin ? '/api/config/editable' : null,
                                                          { quiet: true, refetchOnAuth: true });
  const [saving, setSaving] = React.useState(false);
  const [repeat, setRepeat] = React.useState('');
  React.useEffect(() => { if (data) setRepeat(String(data.list_repeat_min)); }, [data && data.list_repeat_min]);

  const fields = {};
  ((cfg && cfg.fields) || []).forEach((f) => { fields[f.name] = f; });
  const { data: pub } = useApiObject('/api/config', { quiet: true });
  const internet = (data && data.internet) || {};
  const internetOff = !!(pub && pub.internet_access === 'never');

  const save = async (changes, said) => {
    setSaving(true);
    try {
      await apiFetch('/api/music/mix', { method: 'PUT', body: JSON.stringify(changes) });
      fire(said, { tone: 'ok' });
      refresh();
    } catch (e) {
      fire(`not saved: ${(e && e.message) || 'the server said no'}`, { tone: 'warn' });
    } finally { setSaving(false); }
  };
  const saveSwitch = async (name, on, said) => {
    setSaving(true);
    try {
      const res = await apiPatch('/api/config/editable', { changes: { [name]: on } });
      const why = res && res.rejected && res.rejected[name];
      if (why) fire(`not saved: ${why}`, { tone: 'warn' }); else fire(said, { tone: 'ok' });
      refreshCfg(); refresh();
    } catch (e) {
      fire(`not saved: ${(e && e.message) || 'the server said no'}`, { tone: 'warn' });
    } finally { setSaving(false); }
  };

  if (!data) return <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>loading…</div>;
  const [lo, hi] = data.list_repeat_min_range || [20, 500];
  const repeatN = Number(repeat);
  const repeatOk = Number.isInteger(repeatN) && repeatN >= lo && repeatN <= hi;

  // The internet switches: the admin's live value, else what the core last
  // reported. Not admin: shown, not changeable, and says so.
  const swValue = (name, reported) => (fields[name] ? !!fields[name].value : reported);
  const switches = [
    { name: 'music_similar_artists_enabled', label: 'Similar artists from the internet',
      sub: 'Up next can play songs you have by artists like the one playing. Asks ListenBrainz with your artists’ MusicBrainz ids only — no names, nothing you played. Up next never waits for it.',
      value: swValue('music_similar_artists_enabled', internet.similar_artists) },
    { name: 'music_popularity_enabled', label: 'Song popularity from the internet',
      sub: 'An artist’s well-known songs come up a little more often (your own taste counts more). Asks ListenBrainz with your songs’ MusicBrainz ids only.',
      value: swValue('music_popularity_enabled', internet.popularity) },
  ];

  return (
    <div className="music-mix" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {toastNode}
      <div style={{ fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
        How Up next picks the songs that follow when the queue runs out — in every room, browser and phone.
        {' '}Up next leans a little toward songs you haven’t heard in a while; each song in the queue can tell you why it was picked.
        {' '}<a href="#manual/up-next">More in the manual</a>.
      </div>

      <Card title="The mix" sub="People can set their own in Settings → People; everyone else gets these.">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div>
            <div className="label" style={{ marginBottom: 6 }}>discovery or favorites</div>
            <MixSlider id="mix-df" spec={MIX_DISCOVERY} value={data.discovery_favorites} disabled={saving}
                       onChange={(v) => save({ discovery_favorites: v }, `the mix: ${MIX_DISCOVERY.words[v]}`)}/>
          </div>
          <div>
            <div className="label" style={{ marginBottom: 6 }}>stay close or wander</div>
            <MixSlider id="mix-cw" spec={MIX_WANDER} value={data.close_wander} disabled={saving}
                       onChange={(v) => save({ close_wander: v }, `the mix: ${MIX_WANDER.words[v]}`)}/>
          </div>
        </div>
      </Card>

      <Card title="Playing on">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 13, lineHeight: 1.5 }}>
            <input type="checkbox" checked={!!data.autoplay} disabled={saving}
                   onChange={(e) => save({ autoplay: e.target.checked },
                     e.target.checked ? 'similar songs follow when a list ends' : 'the music stops when a list ends')}/>
            <span>
              <strong>Autoplay when a playlist ends</strong>
              <span style={{ display: 'block', color: 'var(--fg-muted)', fontSize: 12 }}>
                Similar songs keep playing when the queue runs out. Rooms and new players start this way;
                a browser or phone can still turn it off for itself in its queue.
              </span>
            </span>
          </label>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
            <label htmlFor="mix-repeat"><strong>No repeats within</strong></label>
            <input id="mix-repeat" type="number" min={lo} max={hi} step={1} value={repeat} disabled={saving}
                   className="cal-inp" style={{ width: 80, textAlign: 'right' }}
                   aria-describedby="mix-repeat-sub" aria-invalid={!repeatOk}
                   onChange={(e) => setRepeat(e.target.value)}
                   onBlur={() => { if (repeatOk && repeatN !== data.list_repeat_min) save({ list_repeat_min: repeatN }, `no repeats within ${repeatN} songs`); }}
                   onKeyDown={(e) => { if (e.key === 'Enter' && repeatOk) save({ list_repeat_min: repeatN }, `no repeats within ${repeatN} songs`); }}/>
            <span>songs</span>
            <span id="mix-repeat-sub" style={{ flexBasis: '100%', color: repeatOk ? 'var(--fg-muted)' : 'var(--warn-ink)', fontSize: 12 }}>
              {repeatOk
                ? 'A list shorter than this plays once, then similar songs follow. A longer one starts over, shuffled again.'
                : `a number from ${lo} to ${hi}`}
            </span>
          </div>
        </div>
      </Card>

      <Card title="From the internet" sub="Optional. Up next works from your library alone without them.">
        {!admin && (
          <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginBottom: 10 }}>
            An admin can change these.{' '}
            <button type="button" className="home-link"
                    onClick={() => { try { Auth.requestLogin(); } catch { /* no auth module */ } }}>sign in</button>
          </div>
        )}
        {internetOff && (
          <div className="chat-note info" role="note" style={{ marginBottom: 10 }}>
            This Domovoi stays off the internet (<a href="#settings/internet">Settings → Internet</a>): these never ask.
          </div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {switches.map((sw) => (
            <label key={sw.name} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 13, lineHeight: 1.5 }}>
              <input type="checkbox" checked={!!sw.value} disabled={!admin || saving || internetOff || sw.value == null}
                     onChange={(e) => saveSwitch(sw.name, e.target.checked,
                       `${sw.label.toLowerCase()}: ${e.target.checked ? 'on' : 'off (what was fetched is deleted)'}`)}/>
              <span>
                <strong>{sw.label}</strong>{sw.value == null && <span style={{ color: 'var(--fg-muted)' }}> · not known yet</span>}
                <span style={{ display: 'block', color: 'var(--fg-muted)', fontSize: 12 }}>{sw.sub}</span>
              </span>
            </label>
          ))}
        </div>
      </Card>
    </div>
  );
};

/* A person's own sliders, in the People page's Profile tab. */
const PersonMusicMix = ({ person, fire }) => {
  const shared = useSharedScreen();
  const { data, refresh } = useApiObject(person ? `/api/people/${person.id}/music-mix` : null, { quiet: true });
  const [saving, setSaving] = React.useState(false);
  if (!person || shared || !data) return null;
  const own = !!data.override;
  const put = async (override, said) => {
    setSaving(true);
    try {
      await apiFetch(`/api/people/${person.id}/music-mix`, { method: 'PUT', body: JSON.stringify({ override }) });
      if (fire) fire(said, { tone: 'ok' });
      refresh();
    } catch (e) {
      if (fire) fire(`not saved: ${(e && e.message) || 'the server said no'}`, { tone: 'warn' });
    } finally { setSaving(false); }
  };
  const uses = data.uses || {};
  const set = (k, v) => put({ discovery_favorites: uses.discovery_favorites, close_wander: uses.close_wander, [k]: v },
                            `${person.name}’s mix saved`);
  return (
    <div style={{ padding: 16, borderBottom: '1px solid var(--border-soft)' }} data-settings-mark="music-mix">
      <div className="label" style={{ marginBottom: 6 }}>music mix</div>
      <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginBottom: own ? 12 : 0 }}>
        <input type="checkbox" checked={!own} disabled={saving}
               onChange={(e) => (e.target.checked
                 ? put(null, `${person.name} uses the household’s mix`)
                 : put({ discovery_favorites: uses.discovery_favorites || 3, close_wander: uses.close_wander || 3 },
                       `${person.name} has their own mix`))}/>
        Use the household’s mix <a href="#settings/music-mix" style={{ fontSize: 12 }}>(see it)</a>
      </label>
      {own && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <MixSlider id={`pmix-df-${person.id}`} spec={MIX_DISCOVERY} value={uses.discovery_favorites} disabled={saving}
                     onChange={(v) => set('discovery_favorites', v)}/>
          <MixSlider id={`pmix-cw-${person.id}`} spec={MIX_WANDER} value={uses.close_wander} disabled={saving}
                     onChange={(v) => set('close_wander', v)}/>
          <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            Used when Up next plays for {person.name}: “listening as” them, their phone, or their voice in a room.
          </div>
        </div>
      )}
    </div>
  );
};

window.PersonMusicMix = PersonMusicMix;
window.DomovoiSettingsSections = window.DomovoiSettingsSections || {};
window.DomovoiSettingsSections['music-mix'] = { Panel: MusicMixPanel };
