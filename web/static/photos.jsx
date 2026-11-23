/* Photos and Faces (1.5.0, media understanding slice 5; design
 * MEDIA-UNDERSTANDING §6, §8, §9 and the owner answers of 2026-11-22).
 *
 * #photos (Media ▸ Photos): every photo and video Domovoi has catalogued,
 * newest first, and a search box for words and times ("dog at the beach",
 * "christmas 2023", "snow last winter"). The grid loads as it scrolls
 * (data.js usePagedList over the search's next_cursor). A photo opens its
 * details; a video opens the player at the moment the search matched
 * (`at_s`), or at the start.
 *
 * #faces (Media ▸ Faces): the people in the photos, on a launcher only
 * while "Recognize faces" is on, and never for a guest or on a shared
 * screen (components.jsx FacesPageStore and navItemsFor). The page itself
 * says the same thing when opened by its address, and asks for nothing.
 *   #faces                — named people, unnamed groups (the small ones,
 *                           mostly strangers, behind "show more"), and for
 *                           an admin the hidden groups;
 *   #faces/person/<id>    — a person's photos and videos, the faces to
 *                           check (confirm, not this person), and Forget
 *                           my face (an admin, or the person's own claimed
 *                           phone or browser);
 *   #faces/group/<id>     — an unnamed group: add a name, hide it, take a
 *                           face out;
 *   #faces/hidden         — an admin's list of hidden groups.
 * Tiers (owner, Q8): the household views, names, confirms, says "not this
 * person" and hides; an admin also merges, splits, moves a named person's
 * faces and shows a hidden group again. The server decides every one
 * (web/backend/api/faces.py); this only leaves off what would be refused.
 * Nothing here asks "who is this?": an unnamed group offers a name field,
 * and the strangers' small groups stay folded away.
 *
 * Every caption, tag and piece of text found in a picture is untrusted
 * text a model or OCR made: rendered only as React text children, never
 * as HTML, and a caption always reads "Might show: …".
 *
 * Data:
 *   GET  /api/media-understanding             · switches and progress (household)
 *   GET  /api/media/search?q=&kind=&cursor=   · the grid and the search (household)
 *   GET  /api/faces/status|people|hidden      · (household; hidden: admin)
 *   GET  /api/faces/groups/{id}               · a group's faces
 *   GET  /api/faces/people/{id}/items         · a person's photos and videos
 *   POST /api/faces/groups/{id}/name|hide, /face/{id}/confirm|not-this-person,
 *        /merge, /split, /people/{id}/forget
 * Thumbnails: /api/images/thumb (photos), /api/videos/poster (videos).
 */

const PHOTOS_SETTINGS_HREF = '#settings/photos-and-videos';
const PHOTOS_PAGE_SIZE = 40;
const PHOTOS_KINDS = [{ id: '', label: 'all' }, { id: 'image', label: 'photos' }, { id: 'video', label: 'videos' }];

const photosThumbUrl = (libraryId, path, size = 'm') => withDeviceToken(
  `${API_BASE}/api/images/thumb?library_id=${encodeURIComponent(libraryId)}`
  + `&path=${encodeURIComponent(path)}&size=${size}`);
const photosRawUrl = (libraryId, path) => withDeviceToken(
  `${API_BASE}/api/images/raw?library_id=${encodeURIComponent(libraryId)}`
  + `&path=${encodeURIComponent(path)}`);
const photosPosterUrl = (libraryId, path) => withDeviceToken(
  `${API_BASE}/api/videos/poster?library_id=${encodeURIComponent(libraryId)}`
  + `&path=${encodeURIComponent(path)}`);

const photosDay = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
};

const photosCount = (n) => (typeof n === 'number' ? n.toLocaleString() : '0');

// A search hit and a person's item, as one shape for the tile and details.
const photosFromHit = (h) => ({
  key: `i${h.id}`, library_id: h.library_id, path: h.path, name: h.name || String(h.path || '').split('/').pop(),
  kind: h.kind, taken_at: h.taken_at, date_from: h.date_from, might_show: h.might_show || null,
  tags: Array.isArray(h.tags) ? h.tags : [], text: h.text || null, analysed: h.analysed !== false,
  at_s: typeof h.at_s === 'number' ? h.at_s : null,
});
const photosFromPersonItem = (it) => ({
  key: `i${it.item_id}`, library_id: it.library_id, path: it.rel_path,
  name: String(it.rel_path || '').split('/').pop(), kind: it.kind, taken_at: it.taken_at, date_from: null,
  might_show: null, tags: [], text: null, analysed: true,
  at_s: Array.isArray(it.moments) && it.moments.length ? it.moments[0] : null,
  confirmed: it.confirmed,
});

/* One tile of the grid. Every string from the server is a text child. */
const PhotosTile = ({ item, onOpen }) => {
  const video = item.kind === 'video';
  return (
    <button type="button" className="photos-tile" onClick={() => onOpen(item)}
            aria-label={`${video ? 'play' : 'open'} ${item.name}`}>
      <span className="photos-tile-img">
        <img src={video ? photosPosterUrl(item.library_id, item.path) : photosThumbUrl(item.library_id, item.path)}
             alt="" loading="lazy" decoding="async" draggable={false}/>
        {video && (
          <span className="photos-tile-badge">
            <Icon name="play" size={12}/>
            {item.at_s != null ? <span className="mono">{fmtDur(item.at_s)}</span> : null}
          </span>
        )}
      </span>
      <span className="photos-tile-text">
        {item.might_show
          ? <span className="photos-tile-cap">Might show: {item.might_show}</span>
          : <span className="photos-tile-cap photos-muted">{item.name}</span>}
        <span className="photos-muted">{photosDay(item.taken_at)}</span>
      </span>
    </button>
  );
};

/* A photo's details: the bigger picture, what it might show, its tags,
 * the text found in it, its date. Esc or the backdrop closes it. */
const PhotosDetails = ({ item, onClose }) => {
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  return (
    <div className="photos-scrim" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={item.name} className="photos-details"
           onClick={(e) => e.stopPropagation()}>
        <div className="photos-details-head">
          <strong className="photos-wrap">{item.name}</strong>
          <IconButton name="x" title="close" aria-label="close" onClick={onClose}/>
        </div>
        <img className="photos-details-img" src={photosThumbUrl(item.library_id, item.path, 'xl')} alt=""/>
        <div className="photos-details-body">
          {item.taken_at && (
            <div className="photos-muted">
              {photosDay(item.taken_at)}{item.date_from === 'file' ? ' (the file’s date)' : ''}
            </div>
          )}
          {item.might_show && <div>Might show: {item.might_show}</div>}
          {item.tags.length > 0 && <div className="photos-muted">{item.tags.join(', ')}</div>}
          {item.text && <div className="photos-found-text">Text in it: {item.text}</div>}
          {!item.analysed && <div className="photos-muted">Not looked at yet: found by its name.</div>}
          {item.might_show && (
            <div className="photos-muted">A description is a computer’s guess at what a picture shows, and is sometimes wrong.</div>
          )}
          <div>
            <a className="btn btn-secondary" href={photosRawUrl(item.library_id, item.path)} target="_blank" rel="noopener noreferrer">
              <Icon name="external-link" size={14}/> open full size
            </a>
          </div>
        </div>
      </div>
    </div>
  );
};

/* The grid, its footer (load as you scroll) and the photo / video it
 * opens. `list` is a usePagedList whose items are already in the tile
 * shape. */
const PhotosGrid = ({ list, noun = 'photos and videos' }) => {
  const [open, setOpen] = React.useState(null);
  const [playing, setPlaying] = React.useState(null);
  const pick = (item) => {
    if (item.kind === 'video') {
      setPlaying({ video: { library_id: item.library_id, rel: item.path, name: item.name },
                   resumeSec: item.at_s || 0 });
    } else setOpen(item);
  };
  return (
    <>
      <div className="photos-grid">
        {list.items.map((it) => <PhotosTile key={it.key} item={it} onOpen={pick}/>)}
      </div>
      <PagedListFooter list={list} noun={noun}/>
      {open && <PhotosDetails item={open} onClose={() => setOpen(null)}/>}
      {playing && typeof VideoOverlay === 'function' && (
        <VideoOverlay video={playing.video} resumeSec={playing.resumeSec} onClose={() => setPlaying(null)}/>
      )}
    </>
  );
};

const PhotosSettingsLink = () => (
  <a className="home-link" href={PHOTOS_SETTINGS_HREF}>Settings → Chat &amp; AI → Photos and videos</a>
);

/* Why there is nothing to show yet, said plainly: understanding is off,
 * or on with nothing looked at so far. Null when there is something. */
const PhotosNotReady = ({ mu, admin }) => {
  const s = (mu && mu.settings) || {};
  const p = (mu && mu.progress) || {};
  const done = ((p.photos && p.photos.done) || 0) + ((p.videos && p.videos.done) || 0);
  const total = ((p.photos && p.photos.total) || 0) + ((p.videos && p.videos.total) || 0);
  if (!s.enabled) {
    return (
      <Empty title="understanding photos and videos is off"
             sub={`Domovoi finds photos and videos by what is in them once it has looked at them, on this server. ${admin ? 'Turn it on' : 'An admin turns it on'} in Settings.`}
             action={<PhotosSettingsLink/>}/>
    );
  }
  if (done === 0) {
    return (
      <Empty title="nothing looked at yet"
             sub={total > 0
               ? `${photosCount(total)} photos and videos are waiting. Domovoi looks at them at night and when the house is quiet; the first pass can take several nights.`
               : 'No photos or videos found yet. Put them in the Pictures or Videos library and Domovoi looks at them at night.'}
             action={<PhotosSettingsLink/>}/>
    );
  }
  return null;
};

/* The people row above the grid while faces are on: each named person,
 * a link to their page. */
const PhotosPeopleRow = () => {
  const { data } = useApiObject('/api/faces/people', { quiet: true });
  const people = (data && data.people) || [];
  if (!people.length) return null;
  return (
    <div className="photos-people-row" aria-label="people">
      {people.map((p) => (
        <a key={p.id} className="photos-person-chip" href={`#faces/person/${p.id}`}>
          <FacesPersonThumb groupId={p.group_id} size={28}/>
          <span className="photos-wrap">{p.name}</span>
        </a>
      ))}
      <a className="photos-person-chip" href="#faces">all faces</a>
    </div>
  );
};

const PhotosPage = () => {
  const guest = useGuestViewer();
  const shared = useSharedScreen();
  const facesOn = useFacesPage();
  const admin = useAdminSignedIn();
  const { data: mu, error: muErr } = useApiObject(guest ? null : '/api/media-understanding',
    { quiet: true, eventTypes: ['media_understanding.changed'] });
  const [q, setQ] = React.useState('');
  const [asked, setAsked] = React.useState('');
  const [kind, setKind] = React.useState('');

  const p = (mu && mu.progress) || {};
  const done = ((p.photos && p.photos.done) || 0) + ((p.videos && p.videos.done) || 0);
  const ready = !!(mu && mu.settings && mu.settings.enabled && done > 0);
  const path = ready
    ? `/api/media/search?q=${encodeURIComponent(asked)}${kind ? `&kind=${kind}` : ''}`
    : null;
  const raw = usePagedList(path, { pageSize: PHOTOS_PAGE_SIZE, key: (h) => h.id, quiet: true });
  const list = { ...raw, items: raw.items.map(photosFromHit) };

  const submit = (e) => { if (e && e.preventDefault) e.preventDefault(); setAsked(q.trim()); };
  const clear = () => { setQ(''); setAsked(''); };

  let body;
  if (guest) body = <PairedOnlyEmpty what="the household’s photos and videos"/>;
  else if (muErr && !mu) body = <PairedOnlyEmpty what="the household’s photos and videos"/>;
  else if (!mu) body = <div className="photos-muted">loading…</div>;
  else if (!ready) body = <PhotosNotReady mu={mu} admin={admin}/>;
  else {
    body = (
      <>
        <form className="photos-search" role="search" onSubmit={submit}>
          <input type="search" className="cal-inp" aria-label="Search photos and videos" maxLength={300}
                 placeholder="dog at the beach, christmas 2023, snow last winter" value={q}
                 onChange={(e) => setQ(e.target.value)}/>
          <Button type="submit" variant="primary" icon="search">Search</Button>
          {asked && <Button type="button" icon="x" onClick={clear}>clear</Button>}
        </form>
        <div className="photos-kinds" role="group" aria-label="show">
          {PHOTOS_KINDS.map((k) => (
            <button key={k.id || 'all'} type="button" aria-pressed={kind === k.id}
                    className={`btn btn-${kind === k.id ? 'primary' : 'secondary'}`}
                    onClick={() => setKind(k.id)}>{k.label}</button>
          ))}
        </div>
        {facesOn && !shared && <PhotosPeopleRow/>}
        {!list.loading && !list.error && list.items.length === 0 && (
          asked
            ? <Empty title="nothing found" sub="Try other words, or a time like “last summer” or “2023”."/>
            : <Empty title="no photos or videos yet" sub="Put them in the Pictures or Videos library." />
        )}
        <PhotosGrid list={list}/>
        {list.items.some((it) => it.might_show) && (
          <div className="photos-muted">Descriptions are a computer’s guess at what a picture shows, and are sometimes wrong.</div>
        )}
      </>
    );
  }

  return (
    <div className="page photos-page">
      <PageHeader title="Photos" sub="your photos and videos, by what is in them and when"/>
      {body}
    </div>
  );
};

/* ── Faces ───────────────────────────────────────────────────────────── */

/* One face, cropped from its photo's thumbnail by its box (fractions of
 * the picture: x, y, w, h), with a margin around it. A face in a video
 * shows the video's poster instead (there is no frame to crop from). */
const FacesCrop = ({ face, size = 72 }) => {
  if (!face) return <span className="faces-crop" style={{ width: size, height: size }} aria-hidden="true"/>;
  if (face.kind === 'video') {
    return (
      <span className="faces-crop" style={{ width: size, height: size }}>
        <img src={photosPosterUrl(face.library_id, face.rel_path)} alt="" loading="lazy"
             style={{ width: '100%', height: '100%', objectFit: 'cover' }}/>
      </span>
    );
  }
  const b = Array.isArray(face.box) && face.box.length === 4 ? face.box : [0, 0, 1, 1];
  const pad = 0.35;
  const w = Math.min(1, b[2] * (1 + 2 * pad));
  const h = Math.min(1, b[3] * (1 + 2 * pad));
  const x = Math.max(0, Math.min(1 - w, b[0] - b[2] * pad));
  const y = Math.max(0, Math.min(1 - h, b[1] - b[3] * pad));
  const pos = (v, span) => (span >= 1 ? 0 : (v / (1 - span)) * 100);
  return (
    <span className="faces-crop" role="img" aria-label="a face"
          style={{ width: size, height: size,
                   backgroundImage: `url("${photosThumbUrl(face.library_id, face.rel_path, 'l')}")`,
                   backgroundSize: `${100 / w}% ${100 / h}%`,
                   backgroundPosition: `${pos(x, w)}% ${pos(y, h)}%` }}/>
  );
};

// A person's or group's best face (the server lists confirmed, then by quality).
const FacesPersonThumb = ({ groupId, size = 72 }) => {
  const { data } = useApiObject(groupId ? `/api/faces/groups/${groupId}?limit=1` : null, { quiet: true });
  const face = data && Array.isArray(data.faces) ? data.faces[0] : null;
  return <FacesCrop face={face} size={size}/>;
};

// The view the address names: #faces[/person/<id> | /group/<id> | /hidden].
const facesViewOf = (route) => {
  const parts = routeParts(route);
  if (parts[0] !== 'faces') return { at: 'home' };
  const id = Number(parts[2]);
  if ((parts[1] === 'person' || parts[1] === 'group') && Number.isInteger(id) && id > 0) return { at: parts[1], id };
  if (parts[1] === 'hidden') return { at: 'hidden' };
  return { at: 'home' };
};
const facesHref = (v) => (v.at === 'home' ? '#faces' : v.at === 'hidden' ? '#faces/hidden' : `#faces/${v.at}/${v.id}`);

const facesErr = (e) => {
  const d = e && e.detail && e.detail.detail;
  return typeof d === 'string' ? d : apiErrorText(e);
};

/* A face's file and, in a video, its appearance (first to last sighting),
 * as a line under it. */
const facesSpan = (face) => {
  if (face.at_s == null) return '';
  const end = typeof face.end_s === 'number' ? face.end_s : null;
  return end != null && end - face.at_s >= 1 ? `${fmtDur(face.at_s)}–${fmtDur(end)}` : fmtDur(face.at_s);
};
const FacesFaceLabel = ({ face }) => (
  <span className="photos-muted photos-wrap faces-face-label">
    {String(face.rel_path || '').split('/').pop()}
    {face.at_s != null ? ` · ${facesSpan(face)}` : ''}
  </span>
);

/* A grid of faces with per-face actions (`actions(face)`), and checkboxes
 * when `selectable` (an admin's split). */
const FacesFaceGrid = ({ faces, actions, selectable, selected, onToggle }) => (
  <div className="faces-grid">
    {faces.map((f) => (
      <div key={f.face_id} className="faces-face">
        <FacesCrop face={f} size={96}/>
        <FacesFaceLabel face={f}/>
        {f.suggested && <span className="photos-muted">suggested</span>}
        <div className="faces-face-acts">
          {selectable && (
            <label className="faces-pick">
              <input type="checkbox" checked={!!selected[f.face_id]} onChange={() => onToggle(f.face_id)}/>
              <span>select</span>
            </label>
          )}
          {actions(f)}
        </div>
      </div>
    ))}
  </div>
);

/* Name a group: an existing person (the same people voice ID knows) or a
 * new name. */
const FacesNameForm = ({ label, busy, onName }) => {
  const [name, setName] = React.useState('');
  const { items: people } = useApiList('/api/people', { quiet: true });
  const send = (e) => {
    if (e && e.preventDefault) e.preventDefault();
    const n = name.trim();
    if (!n) return;
    const hit = (people || []).find((p) => p && typeof p.name === 'string' && p.name.trim().toLowerCase() === n.toLowerCase());
    onName(hit ? { person_id: hit.id } : { name: n });
  };
  return (
    <form className="faces-name-form" onSubmit={send}>
      <input className="cal-inp" aria-label={label} placeholder="a name" list="faces-people" value={name}
             maxLength={80} onChange={(e) => setName(e.target.value)}/>
      <datalist id="faces-people">
        {(people || []).map((p) => <option key={p.id} value={p.name}/>)}
      </datalist>
      <Button type="submit" variant="primary" icon="check" disabled={busy || !name.trim()}>{label}</Button>
    </form>
  );
};

/* Merge another group into this one (admin): unnamed groups and other
 * people. */
const FacesMergeForm = ({ into, overview, busy, onMerge }) => {
  const [pick, setPick] = React.useState('');
  const options = [
    ...((overview && overview.people) || []).filter((p) => p.group_id !== into)
      .map((p) => ({ id: p.group_id, label: `${p.name} (${photosCount(p.faces)} faces)` })),
    ...((overview && overview.groups) || []).filter((g) => g.group_id !== into)
      .map((g) => ({ id: g.group_id, label: `unnamed group ${g.group_id} (${photosCount(g.faces)} faces)` })),
  ];
  if (!options.length) return null;
  return (
    <div className="faces-name-form">
      <select className="cal-inp" aria-label="merge which group into this one" value={pick}
              onChange={(e) => setPick(e.target.value)}>
        <option value="">same person as…</option>
        {options.map((o) => <option key={o.id} value={String(o.id)}>{o.label}</option>)}
      </select>
      <Button icon="merge" disabled={busy || !pick} onClick={() => onMerge(Number(pick))}>merge</Button>
    </div>
  );
};

const FacesOverview = ({ admin, go }) => {
  const [more, setMore] = React.useState(false);
  const { data, error } = useApiObject(`/api/faces/people${more ? '?more=true' : ''}`, { quiet: true });
  if (error && !data) return <div className="photos-muted">couldn’t load faces: {facesErr(error)}</div>;
  if (!data) return <div className="photos-muted">loading…</div>;
  const people = data.people || [];
  const groups = data.groups || [];
  const min = data.shown_min_faces || 3;
  return (
    <>
      <Card title="people" sub="named in your photos and videos">
        {people.length === 0
          ? <div className="photos-muted">nobody named yet</div>
          : (
            <div className="faces-cards">
              {people.map((p) => (
                <a key={p.id} className="faces-card" href={`#faces/person/${p.id}`}
                   onClick={(e) => { e.preventDefault(); go({ at: 'person', id: p.id }); }}>
                  <FacesPersonThumb groupId={p.group_id}/>
                  <strong className="photos-wrap">{p.name}</strong>
                  <span className="photos-muted">
                    {photosCount(p.faces)} faces{p.faces > p.confirmed ? ` · ${photosCount(p.faces - p.confirmed)} to check` : ''}
                  </span>
                </a>
              ))}
            </div>
          )}
      </Card>
      <Card title="unnamed groups" sub="the same face across photos; add a name to the ones you know">
        {groups.length === 0
          ? <div className="photos-muted">{more ? 'no unnamed groups' : 'no unnamed groups yet'}</div>
          : (
            <div className="faces-cards">
              {groups.map((g) => (
                <a key={g.group_id} className="faces-card" href={`#faces/group/${g.group_id}`}
                   onClick={(e) => { e.preventDefault(); go({ at: 'group', id: g.group_id }); }}>
                  <FacesPersonThumb groupId={g.group_id}/>
                  <span className="photos-muted">{photosCount(g.faces)} faces</span>
                </a>
              ))}
            </div>
          )}
        <div className="faces-row">
          <Button icon={more ? 'chevron-up' : 'chevron-down'} onClick={() => setMore(!more)}>
            {more ? 'show fewer' : 'show more'}
          </Button>
          <span className="photos-muted">
            {more ? 'Every group, the small ones too.' : `Groups of fewer than ${min} faces are folded away: mostly people in the background.`}
          </span>
        </div>
      </Card>
      {admin && (
        <div className="faces-row">
          <a className="home-link" href="#faces/hidden"
             onClick={(e) => { e.preventDefault(); go({ at: 'hidden' }); }}>hidden groups</a>
        </div>
      )}
    </>
  );
};

const FacesHidden = ({ go, fire }) => {
  const { data, error, refresh } = useApiObject('/api/faces/hidden', { quiet: true });
  const [busy, setBusy] = React.useState(false);
  const unhide = async (gid) => {
    setBusy(true);
    try { await apiPost(`/api/faces/groups/${gid}/hide`, { hidden: false }); fire('shown again', { tone: 'ok' }); refresh(); }
    catch (e) { fire(`not shown: ${facesErr(e)}`, { tone: 'warn' }); }
    finally { setBusy(false); }
  };
  if (error && !data) return <div className="photos-muted">couldn’t load hidden groups: {facesErr(error)}</div>;
  if (!data) return <div className="photos-muted">loading…</div>;
  const groups = data.groups || [];
  return (
    <Card title="hidden groups" sub="hidden by someone in the house; only an admin sees them">
      {groups.length === 0 ? <div className="photos-muted">none</div> : (
        <div className="faces-cards">
          {groups.map((g) => (
            <div key={g.group_id} className="faces-card">
              <FacesPersonThumb groupId={g.group_id}/>
              <span className="photos-muted">{photosCount(g.faces)} faces</span>
              <Button icon="eye" disabled={busy} onClick={() => unhide(g.group_id)}>show again</Button>
              <a className="home-link" href={`#faces/group/${g.group_id}`}
                 onClick={(e) => { e.preventDefault(); go({ at: 'group', id: g.group_id }); }}>open</a>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
};

/* An unnamed (or hidden) group. */
const FacesGroup = ({ id, admin, go, fire }) => {
  const { data, error, refresh } = useApiObject(`/api/faces/groups/${id}`, { quiet: true });
  const { data: overview } = useApiObject(admin ? '/api/faces/people?more=true' : null, { quiet: true });
  const [busy, setBusy] = React.useState(false);
  const [selected, setSelected] = React.useState({});
  const call = async (fn, said, after) => {
    setBusy(true);
    try {
      const out = await fn();
      if (said) fire(said, { tone: 'ok' });
      if (after) after(out); else refresh();
    } catch (e) { fire(`not done: ${facesErr(e)}`, { tone: 'warn' }); }
    finally { setBusy(false); }
  };
  if (error && !data) return <div className="photos-muted">couldn’t load this group: {facesErr(error)}</div>;
  if (!data) return <div className="photos-muted">loading…</div>;
  if (data.person_id != null) {
    // A named person's group lives on their page.
    return <a className="home-link" href={`#faces/person/${data.person_id}`}
              onClick={(e) => { e.preventDefault(); go({ at: 'person', id: data.person_id }); }}>open this person</a>;
  }
  const faces = data.faces || [];
  const picked = Object.keys(selected).filter((k) => selected[k]).map(Number);
  return (
    <>
      <Card title={data.hidden ? 'hidden group' : 'unnamed group'} sub={`${photosCount(faces.length)} faces`}>
        <div className="faces-col">
          {!data.hidden && (
            <FacesNameForm label="add name" busy={busy}
                           onName={(body) => call(() => apiPost(`/api/faces/groups/${id}/name`, body), 'named',
                                                  (out) => go({ at: 'person', id: out && out.person_id }))}/>
          )}
          <div className="faces-row">
            {!data.hidden && (
              <Button icon="eye-off" disabled={busy}
                      onClick={() => call(() => apiPost(`/api/faces/groups/${id}/hide`, { hidden: true }), 'hidden',
                                          () => go({ at: 'home' }))}>hide this group</Button>
            )}
            {data.hidden && admin && (
              <Button icon="eye" disabled={busy}
                      onClick={() => call(() => apiPost(`/api/faces/groups/${id}/hide`, { hidden: false }), 'shown again')}>show again</Button>
            )}
            {admin && picked.length > 0 && (
              <Button icon="split" disabled={busy}
                      onClick={() => call(() => apiPost('/api/faces/split', { face_ids: picked }), 'moved to a new group',
                                          () => { setSelected({}); refresh(); })}>split selected</Button>
            )}
          </div>
          {admin && (
            <FacesMergeForm into={id} overview={overview} busy={busy}
                            onMerge={(other) => call(() => apiPost('/api/faces/merge', { into: id, groups: [other] }), 'merged')}/>
          )}
        </div>
      </Card>
      <FacesFaceGrid faces={faces} selectable={admin} selected={selected}
                     onToggle={(fid) => setSelected({ ...selected, [fid]: !selected[fid] })}
                     actions={(f) => (
                       <Button disabled={busy}
                               onClick={() => call(() => apiPost('/api/faces/split', { face_ids: [f.face_id] }), 'taken out of this group')}>
                         not the same person
                       </Button>
                     )}/>
    </>
  );
};

/* A named person: their photos and videos, the faces to check, and the
 * admin's tools and Forget my face. */
const FacesPerson = ({ id, admin, go, fire }) => {
  const { data: overview, refresh: refreshOverview } = useApiObject('/api/faces/people?more=true', { quiet: true });
  const person = ((overview && overview.people) || []).find((p) => p.id === id) || null;
  const gid = person ? person.group_id : null;
  const { data: group, refresh: refreshGroup } = useApiObject(gid ? `/api/faces/groups/${gid}` : null, { quiet: true });
  const raw = usePagedList(`/api/faces/people/${id}/items`, { pageSize: PHOTOS_PAGE_SIZE, key: (it) => it.item_id, quiet: true });
  const items = { ...raw, items: raw.items.map(photosFromPersonItem) };
  const [busy, setBusy] = React.useState(false);
  const [selected, setSelected] = React.useState({});
  const [moving, setMoving] = React.useState(false);
  let owner = null;
  try { owner = typeof DeviceIdentity !== 'undefined' && DeviceIdentity.owner ? DeviceIdentity.owner() : null; } catch { owner = null; }
  const mine = !!(owner && owner.id === id);

  const refresh = () => { refreshOverview(); refreshGroup(); if (raw.reload) raw.reload(); };
  const call = async (fn, said, after) => {
    setBusy(true);
    try {
      const out = await fn();
      if (said) fire(said, { tone: 'ok' });
      if (after) after(out); else refresh();
    } catch (e) { fire(`not done: ${facesErr(e)}`, { tone: 'warn' }); }
    finally { setBusy(false); }
  };
  const forget = useDeleteConfirm(() => call(() => apiPost(`/api/faces/people/${id}/forget`, {}),
                                             'face forgotten', () => go({ at: 'home' })));

  if (!overview) return <div className="photos-muted">loading…</div>;
  if (!person) {
    return <Empty title="no faces for this person" sub="Their face may have been forgotten, or never named."
                  action={<a className="home-link" href="#faces" onClick={(e) => { e.preventDefault(); go({ at: 'home' }); }}>all faces</a>}/>;
  }
  const faces = (group && group.faces) || [];
  const toCheck = faces.filter((f) => f.suggested);
  const picked = Object.keys(selected).filter((k) => selected[k]).map(Number);
  const name = person.name;

  return (
    <>
      <div className="faces-person-head">
        <FacesPersonThumb groupId={gid} size={64}/>
        <div className="faces-col">
          <strong className="photos-wrap faces-person-name">{name}</strong>
          <span className="photos-muted">{photosCount(person.faces)} faces · {photosCount(person.confirmed)} confirmed</span>
        </div>
      </div>

      {toCheck.length > 0 && (
        <Card title="to check" sub={`faces Domovoi thinks are ${name}`}>
          <FacesFaceGrid faces={toCheck} actions={(f) => (
            <>
              <Button variant="primary" icon="check" disabled={busy}
                      onClick={() => call(() => apiPost(`/api/faces/face/${f.face_id}/confirm`, { person_id: id }), `confirmed: ${name}`)}>
                yes, {name}
              </Button>
              <Button icon="x" disabled={busy}
                      onClick={() => call(() => apiPost(`/api/faces/face/${f.face_id}/not-this-person`, {}), 'taken off')}>
                not this person
              </Button>
            </>
          )}/>
        </Card>
      )}

      <Card title="photos and videos" sub={`everything ${name} is in`}>
        {!items.loading && !items.error && items.items.length === 0
          ? <div className="photos-muted">none yet</div>
          : <PhotosGrid list={items}/>}
      </Card>

      {admin && (
        <Card title="admin" sub="these change whose faces are whose">
          <div className="faces-col">
            <FacesMergeForm into={gid} overview={overview} busy={busy}
                            onMerge={(other) => call(() => apiPost('/api/faces/merge', { into: gid, groups: [other] }), 'merged')}/>
            {moving
              ? <FacesNameForm label="move faces" busy={busy}
                               onName={(body) => call(() => apiPost(`/api/faces/groups/${gid}/name`, body), 'faces moved',
                                                      (out) => { setMoving(false); go({ at: 'person', id: out && out.person_id }); })}/>
              : <div><Button icon="user-round-pen" disabled={busy} onClick={() => setMoving(true)}>move these faces to someone else</Button></div>}
            <div className="photos-muted">Pick faces below to split them off into a new unnamed group.</div>
            <FacesFaceGrid faces={faces} selectable selected={selected}
                           onToggle={(fid) => setSelected({ ...selected, [fid]: !selected[fid] })}
                           actions={() => null}/>
            {picked.length > 0 && (
              <div><Button icon="split" disabled={busy}
                           onClick={() => call(() => apiPost('/api/faces/split', { face_ids: picked }), 'split into a new group',
                                               () => { setSelected({}); refresh(); })}>split selected</Button></div>
            )}
          </div>
        </Card>
      )}

      {(admin || mine) && (
        <Card title="forget" sub={mine && !admin ? 'this phone or browser is yours' : undefined}>
          <div className="faces-col">
            <div className="photos-muted">
              Deletes every saved face of {name}. Their photos and videos stay.
            </div>
            <div>
              <Button icon="trash-2" disabled={busy}
                      onClick={() => forget.request(person, {
                        title: mine ? 'Forget my face?' : `Forget ${name}’s face?`,
                        confirmLabel: 'Forget face',
                        body: (
                          <>
                            <div>This deletes every saved face of {name} and the groups they are in, and keeps a face pattern tied to nobody, so new photos of them aren’t grouped or suggested again.</div>
                            <div>It’s permanent: {name} stays forgotten even if faces are later turned off and on again (turning faces off keeps the forget list).</div>
                            <div>Their photos and videos stay, and so does {name} in People (voice, memories).</div>
                            <div>This can’t be undone.</div>
                          </>
                        ),
                      })}>
                {mine ? 'Forget my face' : `Forget ${name}’s face`}
              </Button>
            </div>
          </div>
        </Card>
      )}
      {forget.node}
    </>
  );
};

const FacesPage = () => {
  const guest = useGuestViewer();
  const shared = useSharedScreen();
  const admin = useAdminSignedIn();
  const [fire, toastNode] = useToast();
  const [view, setView] = React.useState(() => facesViewOf(currentRoute()));
  React.useEffect(() => {
    const follow = () => {
      const r = currentRoute();
      if (routePage(r) === 'faces') setView(facesViewOf(r));
    };
    window.addEventListener('hashchange', follow);
    return () => window.removeEventListener('hashchange', follow);
  }, []);
  const go = (v) => {
    if (!v || (v.at !== 'home' && v.at !== 'hidden' && !v.id)) v = { at: 'home' };
    setView(v);
    try { if (currentRoute() !== facesHref(v).slice(1)) window.location.hash = facesHref(v).slice(1); } catch { /* no location */ }
  };
  // Guests and shared screens: nothing is asked for, nothing is shown.
  const blocked = guest || shared;
  const { data: st, error } = useApiObject(blocked ? null : '/api/faces/status', { quiet: true });

  let body;
  if (guest) body = <PairedOnlyEmpty what="the faces in the household’s photos"/>;
  else if (shared) body = <Empty title="not on a shared screen" sub="Faces and the people in your photos aren’t shown on a shared screen."/>;
  else if (error && !st) body = <PairedOnlyEmpty what="the faces in the household’s photos"/>;
  else if (!st) body = <div className="photos-muted">loading…</div>;
  else if (!st.enabled) {
    body = (
      <Empty title="recognizing faces is off"
             sub={`Domovoi can group the same face across your photos and videos, on this server, so you can name people. ${admin ? 'Turn it on' : 'An admin turns it on'} in Settings.`}
             action={<PhotosSettingsLink/>}/>
    );
  } else {
    const back = view.at !== 'home'
      ? <Button icon="arrow-left" onClick={() => go({ at: 'home' })}>all faces</Button> : null;
    body = (
      <>
        {!st.models_ready && (
          <div className="photos-muted">
            {st.fetch_error ? `the face models aren’t here yet: ${st.fetch_error}` : 'getting the face models ready…'}
          </div>
        )}
        {back && <div className="faces-row">{back}</div>}
        {view.at === 'home' && <FacesOverview admin={admin} go={go}/>}
        {view.at === 'hidden' && (admin ? <FacesHidden go={go} fire={fire}/> : <div className="photos-muted">only an admin sees hidden groups</div>)}
        {view.at === 'group' && <FacesGroup key={`g${view.id}`} id={view.id} admin={admin} go={go} fire={fire}/>}
        {view.at === 'person' && <FacesPerson key={`p${view.id}`} id={view.id} admin={admin} go={go} fire={fire}/>}
      </>
    );
  }
  return (
    <div className="page photos-page">
      {toastNode}
      <PageHeader title="Faces" sub="the people in your photos and videos; only this household sees them"/>
      {body}
    </div>
  );
};

window.PhotosPage = PhotosPage;
window.FacesPage = FacesPage;
