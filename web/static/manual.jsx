/* User manual — the "#manual" page.
 *
 * The words come from /manual-content.json (web/static/manual-content.json,
 * made by scripts/manual/build_manual.py from scripts/manual/manual.md; the
 * Android app renders the same file). This page draws them:
 *
 *   #manual            Start here: what Domovoi is, things to say, and the
 *                      "Getting set up" checklist, which ticks itself from
 *                      what the server knows.
 *   #manual/map        the map, level 1: "Domovoi" in the middle, the topics
 *                      around it. The centre opens How it works.
 *   #manual/<topic>    level 2: the camera zooms into that topic's pieces.
 *   #manual/<piece>    level 3: the piece's page.
 *
 * The map is the bespoke look of the old "how domovoi works" page, kept:
 * floating nodes, wires that follow them, the name scramble on hover. The
 * camera is one CSS transform on the map's layer (translate + scale), so a
 * click zooms in or out in UM_MOVE_MS (house rule: 300 ms at most); each
 * topic's pieces sit around it at UM_F of full size, so zooming to a topic
 * by 1/UM_F shows them at full size, ringed like the topics are. With
 * prefers-reduced-motion every move is a cut and nothing floats.
 *
 * Live slots fill from the server: the wake word the rooms answer to
 * (GET /api/satellites wake_word / wake_phrase), things to say
 * (/api/capabilities/manual), your rooms, your plugins. "More detail"
 * opens the docs of the version the server runs (/api/config/version's
 * label: the tag when it is a release exactly, else main). Every read is
 * quiet (no sign-in prompt) and a failed one shows nothing extra.
 */

const UM_CONTENT_PATH = '/manual-content.json';
const UM_F = 0.34;                 // a cluster's scale against its parent's
const UM_MOVE_MS = 280;            // the camera's move
const UM_FADE_MS = 180;            // nodes appearing / going
const UM_GLY = 'ABCDEFGHKMNPRSTVXZ0123456789#%&*/<>';
const UM_RELEASE_RE = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$/;
const UM_TOKEN_RE = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\((?:route|node|doc):[^)]+\)|\{(?:admin|preview|plugin|wake)\})/g;
const UM_TOPIC_ICONS = {
  'how-it-works': 'cpu', 'things-to-say': 'message-circle', 'rooms-and-devices': 'radio-tower',
  'your-phone': 'smartphone', 'music-and-media': 'music', 'chat-files-calendar': 'folder',
  'chat-and-help': 'message-square', 'documents-and-letters': 'file-text',
  'people-and-privacy': 'users', 'keeping-it-running': 'wrench',
};

/* ── scoped styles ─────────────────────────────────────────────── */
const UM_CSS = `
.um-wrap { max-width: 1080px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
.um-top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.um-seg { display: inline-flex; border: 1px solid var(--border); border-radius: var(--r-sm); overflow: hidden; flex-shrink: 0; }
.um-seg button { font: inherit; font-size: 12px; padding: 6px 12px; border: none; background: var(--card); color: var(--fg-muted); cursor: pointer; }
.um-seg button + button { border-left: 1px solid var(--border); }
.um-seg button[aria-pressed="true"] { background: var(--brand-soft); color: var(--fg); }
.um-crumbs { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--fg-muted); min-width: 0; flex-wrap: wrap; }
.um-crumbs a { color: var(--fg-muted); text-decoration: none; }
.um-crumbs a:hover { color: var(--fg); }
.um-crumbs .here { color: var(--fg); }
.um-search { position: relative; margin-left: auto; width: 260px; }
.um-search input { width: 100%; height: 32px; box-sizing: border-box; font: inherit; font-size: 13px; padding: 0 10px 0 30px;
  border: 1px solid var(--border); border-radius: var(--r-sm); background: var(--card); color: var(--fg); }
.um-search .ic { position: absolute; left: 9px; top: 8px; color: var(--fg-faint); pointer-events: none; }
.um-results { position: absolute; z-index: 5; top: 36px; left: 0; right: 0; background: var(--overlay, var(--card));
  border: 1px solid var(--border); border-radius: var(--r-md); box-shadow: var(--shadow-md); padding: 4px; margin: 0; list-style: none; }
.um-results li { padding: 7px 10px; border-radius: var(--r-sm); cursor: pointer; display: flex; flex-direction: column; gap: 1px; }
.um-results li[aria-selected="true"], .um-results li:hover { background: var(--brand-soft); }
.um-results .t { font-size: 13px; color: var(--fg); }
.um-results .s { font-size: 11px; color: var(--fg-muted); }
.um-results .none { font-size: 12px; color: var(--fg-muted); padding: 8px 10px; }

.um-stage { position: relative; overflow: hidden; border: 1px solid var(--border); border-radius: var(--r-lg);
  background: var(--sunken); height: 560px; touch-action: manipulation; }
.um-stage.away { display: none; }
.um-layer { position: absolute; left: 0; top: 0; width: 0; height: 0; transform-origin: 0 0;
  transition: transform ${UM_MOVE_MS}ms var(--ease); will-change: transform; }
.um-wires { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; pointer-events: none; }
.um-wire { fill: none; stroke: var(--border-strong); stroke-width: 1.4; stroke-dasharray: 4 4; opacity: 0.75;
  transition: opacity ${UM_FADE_MS}ms var(--ease); }
.um-wire.off { opacity: 0; }
.um-wire.flow { stroke: var(--brand); stroke-width: 1.8; opacity: 1; stroke-dasharray: 6 6; animation: um-wire-flow 0.6s linear infinite; }
@keyframes um-wire-flow { to { stroke-dashoffset: -24; } }

.um-node { position: absolute; transform: translate(-50%, calc(-50% + var(--um-fy, 0px))) scale(var(--um-s, 1));
  font: inherit; text-align: left; cursor: pointer; padding: 10px 12px; background: var(--card); color: var(--fg);
  border: 1px solid var(--border); border-radius: var(--r-md); box-shadow: var(--shadow-sm);
  display: flex; flex-direction: column; gap: 3px; box-sizing: border-box;
  transition: opacity ${UM_FADE_MS}ms var(--ease), visibility 0s, border-color 120ms, background 120ms; }
.um-node.off { opacity: 0; visibility: hidden; pointer-events: none;
  transition: opacity ${UM_FADE_MS}ms var(--ease), visibility 0s linear ${UM_FADE_MS}ms; }
.um-node:hover, .um-node:focus-visible { border-color: var(--brand); background: var(--brand-soft); }
.um-node:focus-visible { outline: 2px solid var(--brand-ring); outline-offset: 2px; }
.um-node.centre, .um-node.hub { border-color: var(--border-strong); }
.um-node.centre .um-name, .um-node.hub .um-name { font-size: 14px; }
.um-nhead { display: flex; align-items: center; gap: 7px; min-width: 0; }
.um-nhead .ic { color: var(--brand-ink); display: inline-flex; }
.um-name { font-family: var(--ff-mono); font-weight: 600; font-size: 13px; letter-spacing: 0.01em; line-height: 1.3; }
.um-sub { font-size: 12px; color: var(--fg-muted); line-height: 1.3; }
.um-node.fixed .um-name, .um-node.fixed .um-sub { display: -webkit-box; -webkit-box-orient: vertical;
  -webkit-line-clamp: 2; overflow: hidden; }
.um-hint { font-size: 11px; color: var(--fg-faint); font-family: var(--ff-mono); }
.um-stage-hint { font-size: 11px; color: var(--fg-faint); margin-top: -8px; }

.um-page, .um-start { background: var(--card); border: 1px solid var(--border); border-radius: var(--r-lg);
  box-shadow: inset 0 1px 0 var(--inner-highlight, transparent); padding: 24px 28px 28px; max-width: 760px;
  animation: um-page-in ${UM_FADE_MS}ms var(--ease); }
@keyframes um-page-in { from { opacity: 0; transform: scale(0.985); } to { opacity: 1; transform: none; } }
.um-eyebrow { font-size: 11px; color: var(--fg-muted); text-transform: lowercase; letter-spacing: 0.02em; }
.um-eyebrow a { color: var(--fg-muted); }
.um-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 4px 0 14px; }
.um-title h1 { margin: 0; font-size: 24px; line-height: 1.2; outline: none; }
.um-intro { color: var(--fg-muted); font-size: 15px; margin: 0 0 12px; }
.um-body { font-size: 13px; line-height: 1.6; color: var(--fg); }
.um-body p { margin: 0 0 10px; }
.um-body ul, .um-body ol { margin: 0 0 12px; padding-left: 20px; }
.um-body li { margin: 3px 0; }
.um-body a { color: var(--brand-ink); }
.um-body code { font-family: var(--ff-mono); font-size: 12px; background: var(--sunken); border: 1px solid var(--border-soft);
  border-radius: 4px; padding: 0 4px; }
.um-label { font-size: 12px; font-weight: 600; color: var(--fg); margin: 16px 0 6px; display: flex; align-items: center; gap: 8px; }
.um-label.wrong { color: var(--warn-ink); }
.um-label .note { font-weight: 400; color: var(--fg-muted); }
.um-say { list-style: none; padding: 0 !important; display: flex; flex-direction: column; gap: 4px; }
.um-say li { margin: 0; }
.um-q { font-family: var(--ff-mono); font-size: 12.5px; color: var(--fg); background: var(--sunken);
  border: 1px solid var(--border-soft); border-radius: var(--r-sm); padding: 1px 7px; }
.um-say .note { color: var(--fg-muted); margin-left: 6px; }
.um-wake { font-weight: 600; }
.um-pill { display: inline-flex; align-items: center; font-size: 10.5px; font-weight: 500; color: var(--fg-muted);
  border: 1px solid var(--border); border-radius: var(--r-sm); padding: 0 6px; margin: 0 2px; vertical-align: 1px; line-height: 16px; }
.um-pill.admin { color: var(--warn-ink); border-color: var(--warn-soft); background: var(--warn-soft); }
.um-pill.coming { border-style: dashed; }
.um-table-wrap { overflow-x: auto; margin: 0 0 12px; }
.um-body table { border-collapse: collapse; font-size: 12.5px; min-width: 100%; }
.um-body th, .um-body td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border-soft); vertical-align: top; }
.um-body th { font-weight: 600; color: var(--fg-muted); font-size: 11.5px; }
.um-note { font-size: 12.5px; color: var(--fg); background: var(--brand-soft); border-radius: var(--r-md); padding: 8px 12px; margin: 0 0 12px; }
.um-check { list-style: none; padding: 0 !important; }
.um-check li { display: flex; gap: 10px; align-items: flex-start; margin: 0 0 8px; }
.um-check .box { flex-shrink: 0; margin-top: 2px; color: var(--fg-faint); display: inline-flex; }
.um-check li.done .box { color: var(--ok-ink); }
.um-check li.done .txt { color: var(--fg-muted); }
.um-live { border: 1px solid var(--border-soft); border-radius: var(--r-md); margin: 0 0 12px; }
.um-live-row { display: flex; gap: 10px; align-items: baseline; padding: 8px 12px; border-bottom: 1px solid var(--border-soft); flex-wrap: wrap; }
.um-live-row:last-child { border-bottom: none; }
.um-live-row .k { font-weight: 600; min-width: 140px; }
.um-live-row .v { color: var(--fg-muted); flex: 1; min-width: 180px; display: flex; gap: 6px; flex-wrap: wrap; align-items: baseline; }
.um-live-empty { font-size: 12px; color: var(--fg-muted); padding: 10px 12px; }
.um-more { margin-top: 18px; font-size: 12px; display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
.um-more a { color: var(--brand-ink); }
.um-next { margin-left: auto; }

@media (max-width: 640px) {
  .um-search { width: 100%; margin-left: 0; order: 3; }
  .um-page, .um-start { padding: 18px 16px 20px; }
  .um-title h1 { font-size: 20px; }
  .um-live-row .k { min-width: 0; }
  .um-node { padding: 8px 10px; }
}
@media (prefers-reduced-motion: reduce) {
  .um-layer, .um-node, .um-node.off, .um-wire { transition: none; }
  .um-wire.flow { animation: none; }
  .um-page, .um-start { animation: none; }
}
`;

/* ── the content, loaded once per page load ─────────────────────── */
const UmStore = (() => {
  let doc = null;
  let pending = null;
  return {
    get doc() { return doc; },
    load() {
      if (doc) return Promise.resolve(doc);
      if (!pending) {
        pending = apiGet(UM_CONTENT_PATH, { quiet: true }).then((d) => {
          if (!d || !/^domovoi-manual\//.test(String(d.schema || ''))) throw new Error('not a manual');
          doc = d;
          return d;
        }).catch((e) => { pending = null; throw e; });
      }
      return pending;
    },
  };
})();

const useUmDoc = () => {
  const [doc, setDoc] = React.useState(UmStore.doc);
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => {
    if (doc) return;
    let live = true;
    UmStore.load().then((d) => { if (live) setDoc(d); }).catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, []);
  return { doc, failed };
};

/* ── what the server knows, read once when the manual opens ─────── */
const umQuiet = (path) => apiGet(path, { quiet: true }).then((x) => x, () => null);

const useUmLive = () => {
  const [live, setLive] = React.useState({});
  React.useEffect(() => {
    let on = true;
    const put = (k, v) => { if (on) setLive((s) => Object.assign({}, s, { [k]: v })); };
    umQuiet('/api/satellites').then(async (sats) => {
      const list = Array.isArray(sats) ? sats : null;
      put('sats', list);
      if (!list) { put('roomHeard', null); return; }
      if (!list.length) { put('roomHeard', false); return; }
      const heard = await Promise.all(list.slice(0, 3).map((s) =>
        umQuiet(`/api/satellites/${encodeURIComponent(s.room_id)}/sessions?limit=1`)));
      put('roomHeard', heard.some((h) => Array.isArray(h) && h.length > 0)
        ? true : (heard.every((h) => Array.isArray(h)) ? false : null));
    });
    umQuiet('/api/capabilities/manual').then((v) => put('caps', v));
    umQuiet('/api/capabilities').then((v) => put('features', v && v.features ? v.features : {}));
    umQuiet('/api/plugins').then((v) => put('plugins', v && Array.isArray(v.plugins) ? v.plugins : null));
    umQuiet('/api/config/version').then((v) => put('version', v && typeof v.version === 'string' ? v.version : null));
    umQuiet('/api/auth/status').then((v) => put('auth', v));
    umQuiet('/api/config').then((v) => put('config', v));
    umQuiet('/api/people').then((v) => put('people', Array.isArray(v) ? v : null));
    umQuiet('/api/music/library/stats').then((v) => put('library', v));
    umQuiet('/api/devices').then((v) => put('devices', Array.isArray(v) ? v : null));
    return () => { on = false; };
  }, []);
  return live;
};

/* The docs of the version the server runs: a release exactly → its tag. */
const umDocRef = (label) => (label && UM_RELEASE_RE.test(label) ? `v${label}` : 'main');

const umTitleWords = (s) => String(s || '').replace(/[_-]+/g, ' ').trim()
  .split(/\s+/).filter(Boolean).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

/* The wake word the rooms answer to: the out-of-the-box word while any room
 * answers to it (or nothing is known), else the one most rooms use; and the
 * others, with their rooms, for the note. */
const umWake = (doc, sats) => {
  const ww = doc.wake_word;
  const phraseOf = (s) => (s.wake_phrase && umTitleWords(s.wake_phrase))
    || ww.names[s.wake_word] || umTitleWords(s.wake_word);
  const known = (sats || []).filter((s) => s && s.wake_word);
  const groups = new Map();
  known.forEach((s) => {
    const p = phraseOf(s);
    if (!groups.has(p)) groups.set(p, []);
    groups.get(p).push(s.room_label || s.room_id);
  });
  const fallback = ww.names[ww.default] || umTitleWords(ww.default);
  let lead = fallback;
  if (known.length && !known.some((s) => s.wake_word === ww.default)) {
    lead = [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))[0][0];
  }
  const others = [...groups.entries()].filter(([p]) => p !== lead)
    .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
    .map(([phrase, rooms]) => ({ phrase, rooms }));
  return { lead, others };
};

const umFill = (tpl, vars) => String(tpl).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));

/* ── inline text ─────────────────────────────────────────────────── */
const UmInline = ({ text, ctx }) => {
  const parts = String(text).split(UM_TOKEN_RE);
  return parts.map((p, i) => {
    if (!p) return null;
    if (i % 2 === 0) return <React.Fragment key={i}>{p}</React.Fragment>;
    if (p.startsWith('**')) return <b key={i}>{p.slice(2, -2)}</b>;
    if (p.startsWith('`')) return <code key={i}>{p.slice(1, -1)}</code>;
    if (p === '{admin}') return <span key={i} className="um-pill admin" title={ctx.doc.labels.admin.means}>{ctx.doc.labels.admin.text}</span>;
    if (p === '{preview}') return <PreviewTag key={i}/>;
    if (p === '{plugin}') return <span key={i} className="um-pill" title={ctx.doc.labels.plugin.means}>{ctx.doc.labels.plugin.text}</span>;
    if (p === '{wake}') return <span key={i} className="um-wake">{umFill(ctx.doc.wake_word.quoted, { phrase: ctx.wake.lead })}</span>;
    if (p.startsWith('[')) {
      const m = /^\[([^\]]+)\]\((route|node|doc):([^)]+)\)$/.exec(p);
      const [, label, kind, target] = m;
      if (kind === 'node') return <a key={i} href={`#manual/${target}`}>{label}</a>;
      if (kind === 'doc') {
        return <a key={i} href={ctx.docUrl(target)} target="_blank" rel="noopener noreferrer">{label}</a>;
      }
      const r = ctx.doc.routes[target];
      return r && r.dashboard ? <a key={i} href={r.dashboard}>{label}</a> : <span key={i}>{label}</span>;
    }
    return <i key={i}>{p.slice(1, -1)}</i>;
  });
};

/* ── live slots ──────────────────────────────────────────────────── */
const UmWakeNote = ({ ctx }) => {
  const { others } = ctx.wake;
  if (!others.length) return null;
  const ww = ctx.doc.wake_word;
  const list = others.map((o) => umFill(ww.note_item, { phrase: o.phrase, rooms: o.rooms.join(', ') })).join(ww.note_join);
  return <div className="um-note">{umFill(ww.note, { list })}</div>;
};

const UmRooms = ({ ctx }) => {
  const sats = ctx.live.sats;
  if (!Array.isArray(sats)) return null;
  if (!sats.length) return <div className="um-live"><div className="um-live-empty">{ctx.doc.labels.rooms_none}</div></div>;
  const ww = ctx.doc.wake_word;
  return (
    <div className="um-live">
      {sats.map((s) => {
        const tone = s.status === 'online' ? 'ok' : (s.status === 'waiting' ? 'warn' : 'idle');
        const np = s.now_playing && (s.now_playing.title || s.now_playing.file);
        const word = s.wake_word && ((s.wake_phrase && umTitleWords(s.wake_phrase)) || ww.names[s.wake_word] || umTitleWords(s.wake_word));
        return (
          <a key={s.room_id} className="um-live-row" href="#settings/rooms" style={{ color: 'inherit', textDecoration: 'none' }}>
            <span className="k" style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
              <StatusDot tone={tone} live={s.status === 'online'}/>{s.room_label || s.room_id}
            </span>
            <span className="v">
              <span>{s.status}</span>
              {word && <span>· {umFill(ww.quoted, { phrase: word })}</span>}
              {np && <span>· {np}</span>}
              {s.last_seen_at && <span>· {relTime(s.last_seen_at)}</span>}
            </span>
          </a>
        );
      })}
    </div>
  );
};

const UmPlugins = ({ ctx }) => {
  const plugins = ctx.live.plugins;
  if (!Array.isArray(plugins)) return null;
  if (!plugins.length) return <div className="um-live"><div className="um-live-empty">{ctx.doc.labels.plugins_none}</div></div>;
  return (
    <div className="um-live">
      {plugins.map((p) => (
        <div key={p.slug} className="um-live-row">
          <span className="k">{p.name || p.slug}</span>
          <span className="v">
            <span className="mono">{p.version}</span>
            <Pill tone={p.enabled && p.status === 'ok' ? 'ok' : (p.enabled ? 'warn' : 'idle')}>
              {p.enabled ? p.status : 'disabled'}
            </Pill>
            {(p.pages || []).filter((pg) => pg.route).map((pg) => (
              <a key={pg.route} href={`#${pg.route}`}>{pg.nav_label || pg.route}</a>
            ))}
          </span>
        </div>
      ))}
    </div>
  );
};

/* Every feature the server has, with phrases: the handler's own, else the
 * manual's for that handler (never a pending one). */
const UmThingsToSay = ({ ctx }) => {
  const caps = ctx.live.caps;
  const needs = ctx.doc.handler_needs_feature || {};
  const features = ctx.live.features || {};
  const handlers = caps && Array.isArray(caps.handlers)
    ? caps.handlers.filter((h) => !needs[h.name] || features[needs[h.name]] === true) : null;
  if (!handlers) return null;
  const net = ctx.doc.labels.net;
  return (
    <div className="um-live">
      {handlers.map((h) => {
        const own = (h.example_phrases || []).filter(Boolean);
        const said = own.length ? own : (ctx.phrasesByHandler[h.name] || []);
        return (
          <div key={h.name} className="um-live-row">
            <span className="k">{(h.display && h.display.label) || h.name}</span>
            <span className="v">
              {said.length
                ? said.slice(0, 3).map((p) => <span key={p} className="um-q">“{p}”</span>)
                : <span>{ctx.doc.labels.say_none}</span>}
              {h.origin && h.origin !== 'core' && <span className="um-pill">{ctx.doc.labels.plugin.text}: {h.origin}</span>}
              {net[h.requires_network] && <span className="um-pill">{net[h.requires_network]}</span>}
            </span>
          </div>
        );
      })}
    </div>
  );
};

const UM_SLOTS = { 'wake-word': UmWakeNote, rooms: UmRooms, plugins: UmPlugins, 'things-to-say': UmThingsToSay };

/* ── the checklist ───────────────────────────────────────────────── */

/** What a phone app registers itself as (POST /api/devices/register). */
const PHONE_APP_PLATFORMS = ['android', 'ios'];

const umChecklistDone = (key, live) => {
  const known = (v) => (v === undefined ? null : v);
  switch (key) {
    case 'admin_claimed': return live.auth ? !!live.auth.setup_complete : known(live.auth);
    case 'internet_answered':
      return live.config && typeof live.config.internet_access === 'string'
        ? live.config.internet_access !== '' : null;
    case 'rooms': return Array.isArray(live.sats) ? live.sats.length > 0 : null;
    case 'room_heard': return live.roomHeard === undefined ? null : live.roomHeard;
    case 'people': return Array.isArray(live.people) ? live.people.length > 0 : null;
    case 'music': return live.library && typeof live.library.total_tracks === 'number' ? live.library.total_tracks > 0 : null;
    // Any of our phone apps ticks this, not Android's alone: an iPhone
    // registers as 'ios' (POST /api/devices/register), and before 2026-10-28
    // this read `=== 'android'`, so a house whose only phone app was an
    // iPhone was told it had not put the app on its phones.
    case 'phone_app': return Array.isArray(live.devices)
      ? live.devices.some((d) => PHONE_APP_PLATFORMS.includes(d.platform)) : null;
    default: return null;
  }
};

const UmChecklist = ({ ctx }) => {
  const items = ctx.doc.start.checklist.filter((c) => c.platforms.includes('dashboard'));
  return (
    <ol className="um-check">
      {items.map((c) => {
        const done = umChecklistDone(c.done_when, ctx.live);
        return (
          <li key={c.id} className={done ? 'done' : ''}>
            <span className="box" title={done == null ? ctx.doc.labels.checklist_unknown : undefined}
                  aria-label={done ? 'done' : (done == null ? ctx.doc.labels.checklist_unknown : 'not yet')}>
              <Icon name={done ? 'circle-check' : (done == null ? 'circle-dashed' : 'circle')} size={16}/>
            </span>
            <span className="txt"><UmInline text={c.text} ctx={ctx}/></span>
          </li>
        );
      })}
    </ol>
  );
};

/* ── blocks ──────────────────────────────────────────────────────── */
const umShown = (x) => !x.only || x.only === 'dashboard';

/* A phrase whose `needs` feature the server reports off (features.ai false:
 * AI chat and questions are off) is left out, with its "then" reply; an
 * unknown or older server shows it. */
const umPhraseShown = (p, ctx) => !(p && p.needs && ((ctx.live && ctx.live.features) || {})[p.needs] === false);

const UmSay = ({ phrases: all, ctx }) => {
  const phrases = all.filter((p) => umPhraseShown(p, ctx));
  if (!phrases.length) return null;
  return (
  <ul className="um-say">
    {phrases.map((p, i) => {
      if (p.then) return null;
      const next = phrases[i + 1];
      return (
        <li key={i}>
          <span className="um-q">“{p.text}”</span>
          {next && next.then && <>, then <span className="um-q">“{next.text}”</span></>}
          {p.note && <span className="note"><UmInline text={p.note} ctx={ctx}/></span>}
          {p.pending && <span className="um-pill coming" title={ctx.doc.labels.pending.means}>{ctx.doc.labels.pending.text}</span>}
        </li>
      );
    })}
  </ul>
  );
};

const UmBlocks = ({ blocks, ctx }) => blocks.filter(umShown).map((b, i) => {
  switch (b.type) {
    case 'p': return <p key={i}><UmInline text={b.text} ctx={ctx}/></p>;
    case 'list': {
      const Tag = b.ordered ? 'ol' : 'ul';
      return <Tag key={i}>{b.items.filter(umShown).map((it, j) => <li key={j}><UmInline text={it.text} ctx={ctx}/></li>)}</Tag>;
    }
    case 'label':
      return (
        <div key={i} className={`um-label ${b.role}`}>
          <span>{b.text}</span>
          {b.admin && <span className="um-pill admin" title={ctx.doc.labels.admin.means}>{ctx.doc.labels.admin.text}</span>}
          {b.note && <span className="note">{b.note}</span>}
        </div>
      );
    case 'say': return <UmSay key={i} phrases={b.phrases} ctx={ctx}/>;
    case 'table':
      return (
        <div key={i} className="um-table-wrap">
          <table>
            <thead><tr>{b.header.map((h, j) => <th key={j}><UmInline text={h} ctx={ctx}/></th>)}</tr></thead>
            <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k}><UmInline text={c} ctx={ctx}/></td>)}</tr>)}</tbody>
          </table>
        </div>
      );
    case 'live': {
      const Slot = UM_SLOTS[b.slot];
      return Slot ? <Slot key={i} ctx={ctx}/> : null;
    }
    case 'checklist': return <UmChecklist key={i} ctx={ctx}/>;
    default: return null;
  }
});

/* ── the map's geometry ──────────────────────────────────────────── */
// Node size for a stage `W` wide: on a phone two to a row.
const umDims = (W) => {
  const phone = W < 600;
  // A phone's nodes are one fixed size (two lines of name, two of
  // subtitle), so its rows never run into each other.
  return { phone, NW: phone ? Math.floor((W - 14) / 2) : 196, NH: phone ? 104 : 66 };
};

// n nodes around a cluster's middle, at full size. Wide: an ellipse that
// fills the stage. Phone: rows of two above and below the middle.
const umRing = (n, W, H, d) => {
  if (!d.phone) {
    const rx = Math.max(W / 2 - d.NW / 2 - 14, 150);
    const ry = Math.max(H / 2 - d.NH / 2 - 14, 110);
    return Array.from({ length: n }, (_, i) => {
      const a = (-90 + (i * 360) / n) * (Math.PI / 180);
      return { x: rx * Math.cos(a), y: ry * Math.sin(a) };
    });
  }
  const rows = Math.ceil(n / 2);
  const up = Math.ceil(rows / 2);
  const step = d.NH + 18;
  const dx = d.NW / 2 + 5;
  return Array.from({ length: n }, (_, i) => {
    const r = Math.floor(i / 2);
    const y = (r < up ? -(up - r) : r - up + 1) * step;
    const alone = i === n - 1 && n % 2 === 1;
    return { x: alone ? 0 : (i % 2 ? dx : -dx), y };
  });
};

const umStageHeight = (W) => {
  const d = umDims(W);
  if (!d.phone) return 560;
  return 4 * (d.NH + 18) + d.NH + 32;   // seven nodes: two rows above, two below
};

/* ── the map ─────────────────────────────────────────────────────── */
const UmMap = ({ doc, visiblePieces, focus, away, onGo, nodeRefs }) => {
  const stageRef = React.useRef(null);
  const layerRef = React.useRef(null);
  const wireRefs = React.useRef({});
  const [W, setW] = React.useState(0);
  const reduce = React.useMemo(
    () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches), []);

  React.useLayoutEffect(() => {
    const el = stageRef.current;
    if (!el) return undefined;
    const read = () => setW(el.clientWidth);
    read();
    if (!('ResizeObserver' in window)) { window.addEventListener('resize', read); return () => window.removeEventListener('resize', read); }
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [away]);

  const H = umStageHeight(W || 800);
  const d = umDims(W || 800);
  const centreTopic = doc.centre.opens;
  const ringTopics = doc.topics.filter((t) => t.id !== centreTopic);

  // World positions: the centre, the topics, every cluster's hub and pieces.
  const geo = React.useMemo(() => {
    const nodes = {};   // id → {x, y, s}
    const top = umRing(ringTopics.length, W || 800, H, d);
    nodes.domovoi = { x: 0, y: 0, s: 1 };
    ringTopics.forEach((t, i) => { nodes[`t:${t.id}`] = { x: top[i].x, y: top[i].y, s: 1 }; });
    doc.topics.forEach((t) => {
      const at = t.id === centreTopic ? { x: 0, y: 0 } : nodes[`t:${t.id}`];
      nodes[`h:${t.id}`] = { x: at.x, y: at.y, s: UM_F };
      const kids = t.pieces.filter((p) => visiblePieces.has(p));
      const ring = umRing(kids.length, W || 800, H, d);
      kids.forEach((p, j) => { nodes[`p:${p}`] = { x: at.x + ring[j].x * UM_F, y: at.y + ring[j].y * UM_F, s: UM_F }; });
    });
    return nodes;
  }, [doc, W, H, visiblePieces]);

  const focusTopic = focus ? doc.topics.find((t) => t.id === focus) : null;
  const shown = React.useMemo(() => {
    if (!focusTopic) return new Set(['domovoi', ...ringTopics.map((t) => `t:${t.id}`)]);
    return new Set([`h:${focusTopic.id}`, ...focusTopic.pieces.filter((p) => visiblePieces.has(p)).map((p) => `p:${p}`)]);
  }, [focusTopic, visiblePieces]);

  const edges = React.useMemo(() => {
    const out = ringTopics.map((t) => ['domovoi', `t:${t.id}`]);
    doc.topics.forEach((t) => t.pieces.filter((p) => visiblePieces.has(p)).forEach((p) => out.push([`h:${t.id}`, `p:${p}`])));
    return out;
  }, [doc, visiblePieces]);

  // The camera: level 1 looks at the centre at 1:1, level 2 at a cluster at 1/UM_F.
  const cam = focusTopic ? geo[`h:${focusTopic.id}`] : { x: 0, y: 0 };
  const k = focusTopic ? 1 / UM_F : 1;
  const camera = `translate(${(W || 800) / 2 - cam.x * k}px, ${H / 2 - cam.y * k}px) scale(${k})`;
  // The camera moves (eases) only for a click's zoom: a resize, or the
  // first measure, is a cut, so the map never slides in on open.
  const lastFocus = React.useRef(focus);
  const movedAt = React.useRef(0);
  if (lastFocus.current !== focus) { lastFocus.current = focus; movedAt.current = Date.now(); }
  const easing = !reduce && Date.now() - movedAt.current < UM_MOVE_MS + 120;

  // Float + wires, one frame loop: each shown node bobs, its wires follow.
  React.useEffect(() => {
    const amp = 5;
    const ids = Object.keys(geo);
    const phase = {};
    ids.forEach((id, i) => { phase[id] = { dur: 5.5 + (i % 4) * 0.9, at: i * 0.7 }; });
    const offset = (id, t) => {
      if (reduce || !shown.has(id)) return 0;
      const ph = phase[id];
      return amp * Math.sin((2 * Math.PI * (t + ph.at)) / ph.dur) * geo[id].s;
    };
    // The wires live outside the camera's layer, in the stage's own pixels,
    // so a zoom never thickens them: each frame maps the world through the
    // layer's transform as it is right now (mid-move included).
    const draw = (t) => {
      ids.forEach((id) => {
        const el = nodeRefs.current[id];
        if (el) el.style.setProperty('--um-fy', `${offset(id, t)}px`);
      });
      const layer = layerRef.current;
      if (!layer) return;
      let m;
      try { m = new DOMMatrixReadOnly(getComputedStyle(layer).transform); } catch { return; }
      const X = (x) => m.a * x + m.e;
      const Y = (y) => m.d * y + m.f;
      edges.forEach(([a, b]) => {
        const p = wireRefs.current[`${a}>${b}`];
        if (!p) return;
        const A = geo[a], B = geo[b];
        const ax = X(A.x), bx = X(B.x);
        const ay = Y(A.y + offset(a, t)), by = Y(B.y + offset(b, t));
        const my = (ay + by) / 2;
        p.setAttribute('d', `M${ax} ${ay} C ${ax} ${my} ${bx} ${my} ${bx} ${by}`);
      });
    };
    let raf = 0;
    const loop = (now) => { draw(now / 1000); raf = requestAnimationFrame(loop); };
    draw(performance.now() / 1000);
    if (!reduce && !away) raf = requestAnimationFrame(loop);
    // Still: draw again once a move (if any) has ended.
    const settle = reduce ? 0 : setTimeout(() => draw(performance.now() / 1000), UM_MOVE_MS + 40);
    return () => { if (raf) cancelAnimationFrame(raf); clearTimeout(settle); };
  }, [geo, shown, edges, reduce, away, camera]);

  const scramble = (id) => {
    if (reduce) return;
    const el = nodeRefs.current[id] && nodeRefs.current[id].querySelector('.um-name');
    if (!el) return;
    const final = el.dataset.final || el.textContent;
    el.dataset.final = final;
    let frame = 0;
    const steps = 8;
    clearInterval(el._umT);
    el._umT = setInterval(() => {
      let out = '';
      for (let i = 0; i < final.length; i++) {
        out += final[i] === ' ' || i < (frame / steps) * final.length ? final[i] : UM_GLY[Math.floor(Math.random() * UM_GLY.length)];
      }
      el.textContent = out;
      if (++frame > steps) { clearInterval(el._umT); el.textContent = final; }
    }, 24);
  };
  const flow = (id, on) => edges.forEach(([a, b]) => {
    const p = wireRefs.current[`${a}>${b}`];
    if (p && (a === id || b === id)) p.classList.toggle('flow', on);
  });
  const hover = (id) => ({
    onMouseEnter: () => { flow(id, true); scramble(id); },
    onMouseLeave: () => flow(id, false),
    onFocus: () => { flow(id, true); scramble(id); },
    onBlur: () => flow(id, false),
  });

  const node = (id, cls, icon, title, sub, extra, onClick, label) => {
    const g = geo[id];
    if (!g) return null;
    const on = shown.has(id);
    return (
      <button key={id} type="button" className={`um-node ${cls}${on ? '' : ' off'}${d.phone ? ' fixed' : ''}`}
              ref={(el) => { nodeRefs.current[id] = el; }}
              style={{ left: g.x, top: g.y, width: d.NW, '--um-s': g.s,
                       ...(d.phone ? { height: d.NH } : { minHeight: d.NH }) }}
              tabIndex={on ? 0 : -1} aria-hidden={on ? undefined : true} aria-label={label}
              onClick={onClick} {...hover(id)}>
        <span className="um-nhead">
          {icon && <span className="ic"><Icon name={icon} size={14}/></span>}
          <span className="um-name">{title}</span>
        </span>
        {(sub || extra) && <span className="um-sub">{sub}{extra && <> {extra}</>}</span>}
      </button>
    );
  };

  const pieceById = React.useMemo(() => Object.fromEntries(doc.pieces.map((p) => [p.id, p])), [doc]);
  const centreT = doc.topics.find((t) => t.id === centreTopic);

  return (
    <div className={`um-stage${away ? ' away' : ''}`} ref={stageRef} style={{ height: H }}
         role="group" aria-label="the manual's map">
      <svg className="um-wires" aria-hidden="true">
        {edges.map(([a, b]) => (
          <path key={`${a}>${b}`} className={`um-wire${shown.has(a) && shown.has(b) ? '' : ' off'}`}
                ref={(el) => { wireRefs.current[`${a}>${b}`] = el; }}/>
        ))}
      </svg>
      <div className="um-layer" ref={layerRef} style={{ transform: camera, transition: easing ? undefined : 'none' }}>
        {W > 0 && node('domovoi', 'centre', null, doc.centre.title, doc.centre.subtitle,
              null, () => onGo(centreTopic), `${doc.centre.title}: ${doc.labels.centre_hint}`)}
        {W > 0 && ringTopics.map((t) => node(`t:${t.id}`, 'topic', UM_TOPIC_ICONS[t.id] || 'circle', t.title, t.subtitle,
                                    null, () => onGo(t.id), `${t.title}: ${t.subtitle}`))}
        {W > 0 && doc.topics.map((t) => node(`h:${t.id}`, 'hub', UM_TOPIC_ICONS[t.id] || 'circle', t.title,
                                    t.id === centreTopic ? (centreT && centreT.subtitle) : t.subtitle,
                                    null, () => onGo('map'), `${t.title}: ${doc.labels.zoom_out}`))}
        {W > 0 && doc.pieces.filter((p) => visiblePieces.has(p.id)).map((p) => node(
          `p:${p.id}`, 'piece', null, p.title, p.subtitle,
          <>{p.preview && <PreviewTag/>}{p.plugin && <span className="um-pill">{doc.labels.plugin.text}</span>}</>,
          () => onGo(p.alias_of && pieceById[p.alias_of] ? p.alias_of : p.id), `${p.title}: ${p.subtitle}`))}
      </div>
    </div>
  );
};

/* ── search ──────────────────────────────────────────────────────── */
const umSearch = (doc, visiblePieces, q) => {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const topicOf = Object.fromEntries(doc.topics.map((t) => [t.id, t]));
  const scored = [];
  doc.pieces.filter((p) => visiblePieces.has(p.id) && !p.alias_of).forEach((p) => {
    const title = p.title.toLowerCase();
    const hay = [title, p.subtitle.toLowerCase(), ...p.keywords.map((k) => k.toLowerCase()),
                 topicOf[p.topic].title.toLowerCase()].join(' · ');
    if (!words.every((w) => hay.includes(w))) return;
    let score = 0;
    words.forEach((w) => {
      if (title.startsWith(w)) score += 4;
      else if (title.includes(w)) score += 3;
      else if (p.keywords.some((k) => k.toLowerCase().startsWith(w))) score += 2;
      else score += 1;
    });
    scored.push({ p, score, topic: topicOf[p.topic].title });
  });
  return scored.sort((a, b) => b.score - a.score || a.p.title.localeCompare(b.p.title)).slice(0, 8);
};

const UmSearch = ({ doc, visiblePieces, onGo }) => {
  const [q, setQ] = React.useState('');
  const [sel, setSel] = React.useState(0);
  const [open, setOpen] = React.useState(false);
  const results = React.useMemo(() => umSearch(doc, visiblePieces, q), [doc, visiblePieces, q]);
  const pick = (r) => { if (!r) return; setQ(''); setOpen(false); onGo(r.p.id); };
  const onKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, results.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(results[sel]); }
    else if (e.key === 'Escape') { e.stopPropagation(); setQ(''); setOpen(false); }
  };
  return (
    <div className="um-search">
      <span className="ic"><Icon name="search" size={14}/></span>
      <input type="search" value={q} placeholder={doc.labels.search_placeholder} aria-label={doc.labels.search_placeholder}
             role="combobox" aria-expanded={open && q.trim() !== ''} aria-controls="um-results" aria-autocomplete="list"
             aria-activedescendant={open && results[sel] ? `um-r-${results[sel].p.id}` : undefined}
             onChange={(e) => { setQ(e.target.value); setSel(0); setOpen(true); }}
             onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 120)} onKeyDown={onKey}/>
      {open && q.trim() !== '' && (
        <ul className="um-results" id="um-results" role="listbox">
          {results.length === 0 && <li className="none" role="option" aria-selected="false">{doc.labels.search_empty}</li>}
          {results.map((r, i) => (
            <li key={r.p.id} id={`um-r-${r.p.id}`} role="option" aria-selected={i === sel}
                onMouseDown={(e) => { e.preventDefault(); pick(r); }}>
              <span className="t">{r.p.title}</span>
              <span className="s">{r.topic} · {r.p.subtitle}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

/* ── the address ─────────────────────────────────────────────────── */
// #manual → start; #manual/map → level 1; #manual/<topic> → level 2;
// #manual/<piece> → that page (its topic zoomed behind it).
const umAt = (doc) => {
  const parts = routeParts(currentRoute());
  const id = parts[0] === 'manual' ? parts[1] : null;
  if (!id || !doc || id === 'start') return { view: 'start' };
  if (id === 'map') return { view: 'map', topic: null };
  if (doc.topics.some((t) => t.id === id)) return { view: 'map', topic: id };
  const p = doc.pieces.find((x) => x.id === id);
  if (p) return { view: 'page', topic: p.topic, piece: p.id };
  return { view: 'start' };
};
const umGo = (id) => { try { window.location.hash = id ? `manual/${id}` : 'manual'; } catch { /* no location */ } };

/* ── the page ────────────────────────────────────────────────────── */
const UserManualPage = () => {
  const { doc, failed } = useUmDoc();
  const live = useUmLive();
  const [at, setAt] = React.useState(() => umAt(doc));
  const wrapRef = React.useRef(null);
  const nodeRefs = React.useRef({});
  const headRef = React.useRef(null);
  const moved = React.useRef(false);   // the last change came from here: move the focus with it

  React.useEffect(() => {
    setAt(umAt(doc));
    const follow = () => { if (routePage(currentRoute()) === 'manual') setAt(umAt(doc)); };
    window.addEventListener('hashchange', follow);
    return () => window.removeEventListener('hashchange', follow);
  }, [doc]);

  const go = (id) => { moved.current = true; umGo(id); };

  // Zoom out one level: a page → its topic, a topic → the map.
  const up = React.useCallback(() => {
    if (at.view === 'page') go(at.topic);
    else if (at.view === 'map' && at.topic) go('map');
  }, [at]);
  React.useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
      if (at.view === 'page' || (at.view === 'map' && at.topic)) { e.preventDefault(); up(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [at, up]);

  // Focus follows a move made here: a page's title, a topic's first piece,
  // the map's topic we came out of.
  const prev = React.useRef(at);
  React.useEffect(() => {
    const was = prev.current;
    prev.current = at;
    if (!moved.current || !doc) return;
    moved.current = false;
    const focusLater = (fn) => setTimeout(fn, 30);
    if (at.view === 'page' || at.view === 'start') {
      focusLater(() => { if (headRef.current) headRef.current.focus({ preventScroll: true }); });
      if (wrapRef.current) wrapRef.current.scrollIntoView({ block: 'start' });
    } else if (at.view === 'map' && at.topic) {
      const t = doc.topics.find((x) => x.id === at.topic);
      const first = was.view === 'page' && was.piece ? `p:${was.piece}` : (t && t.pieces[0] ? `p:${t.pieces[0]}` : null);
      focusLater(() => { const el = first && nodeRefs.current[first]; if (el) el.focus({ preventScroll: true }); });
    } else if (at.view === 'map') {
      const back = was.topic ? (was.topic === doc.centre.opens ? 'domovoi' : `t:${was.topic}`) : 'domovoi';
      focusLater(() => { const el = nodeRefs.current[back]; if (el) el.focus({ preventScroll: true }); });
    }
  }, [at, doc]);

  const visiblePieces = React.useMemo(() => {
    if (!doc) return new Set();
    const on = new Set((live.plugins || []).filter((p) => p.enabled && (p.status === 'ok' || p.status === 'degraded')).map((p) => p.slug));
    return new Set(doc.pieces.filter((p) => !p.plugin || on.has(p.plugin)).map((p) => p.id));
  }, [doc, live.plugins]);

  const ctx = React.useMemo(() => {
    if (!doc) return null;
    const ref = umDocRef(live.version);
    const phrasesByHandler = {};
    [doc.start, ...doc.pieces].forEach((pg) => pg.blocks.forEach((b) => {
      if (b.type !== 'say') return;
      b.phrases.forEach((p) => {
        if (!p.handler || p.pending || p.then || p.text.includes('…')) return;
        (phrasesByHandler[p.handler] = phrasesByHandler[p.handler] || []).includes(p.text) || phrasesByHandler[p.handler].push(p.text);
      });
    }));
    return {
      doc, live, phrasesByHandler,
      wake: umWake(doc, live.sats),
      docUrl: (target) => doc.doc_base.replace('{ref}', ref) + target,
    };
  }, [doc, live]);

  if (!doc) {
    return (
      <div className="page">
        <PageHeader title="User manual"/>
        {failed
          ? <Empty title="the manual didn't load" sub="this server's copy can't be read right now; reload to try again"/>
          : <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>loading…</div>}
      </div>
    );
  }

  const topicOf = (id) => doc.topics.find((t) => t.id === id);
  const piece = at.view === 'page' ? doc.pieces.find((p) => p.id === at.piece) : null;
  const topic = at.topic ? topicOf(at.topic) : null;

  const crumbs = at.view === 'start' ? null : (
    <nav className="um-crumbs" aria-label="where you are in the manual">
      {at.view === 'map' && !topic
        ? <span className="here">{doc.centre.title}</span>
        : <a href="#manual/map" onClick={() => { moved.current = true; }}>{doc.centre.title}</a>}
      {topic && <Icon name="chevron-right" size={12}/>}
      {topic && (piece
        ? <a href={`#manual/${topic.id}`} onClick={() => { moved.current = true; }}>{topic.title}</a>
        : <span className="here">{topic.title}</span>)}
      {piece && <Icon name="chevron-right" size={12}/>}
      {piece && <span className="here">{piece.title}</span>}
      {(topic || piece) && (
        <Button variant="ghost" icon="zoom-out" onClick={up} aria-label={doc.labels.zoom_out}
                style={{ height: 26, padding: '0 8px', fontSize: 12 }}>{doc.labels.zoom_out}</Button>
      )}
    </nav>
  );

  const siblings = piece ? topicOf(piece.topic).pieces.filter((id) => visiblePieces.has(id)) : [];
  const nextId = piece ? siblings[siblings.indexOf(piece.id) + 1] : null;
  const nextPiece = nextId ? doc.pieces.find((p) => p.id === nextId) : null;

  return (
    <React.Fragment>
      <style>{UM_CSS}</style>
      <div className="um-wrap" ref={wrapRef}>
        <div className="um-top">
          <div className="um-seg" role="group" aria-label="manual view">
            <button type="button" aria-pressed={at.view === 'start'} onClick={() => go(null)}>{doc.labels.start_here}</button>
            <button type="button" aria-pressed={at.view !== 'start'} onClick={() => go('map')}>{doc.labels.map}</button>
          </div>
          {crumbs}
          <UmSearch doc={doc} visiblePieces={visiblePieces} onGo={go}/>
        </div>

        {at.view === 'start' && (
          <section className="um-start" aria-labelledby="um-h">
            <div className="um-eyebrow">{doc.labels.eyebrow}</div>
            <div className="um-title"><h1 id="um-h" ref={headRef} tabIndex={-1}>{doc.start.title}</h1></div>
            <div className="um-body"><UmBlocks blocks={doc.start.blocks} ctx={ctx}/></div>
            <div className="um-more">
              <Button variant="primary" icon="map" onClick={() => go('map')}>{doc.labels.show_map}</Button>
            </div>
          </section>
        )}

        {at.view !== 'start' && (
          <UmMap doc={doc} visiblePieces={visiblePieces} focus={at.topic || null} away={at.view === 'page'}
                 onGo={(id) => go(id === 'map' ? 'map' : id)} nodeRefs={nodeRefs}/>
        )}

        {at.view === 'map' && !topic && <div className="um-stage-hint">{doc.labels.map_hint}</div>}
        {at.view === 'map' && topic && topic.intro && (
          <p className="um-intro" style={{ margin: 0 }}><UmInline text={topic.intro} ctx={ctx}/></p>
        )}

        {piece && (
          <article className="um-page" aria-labelledby="um-h" key={piece.id}>
            <div className="um-eyebrow">
              <a href={`#manual/${piece.topic}`} onClick={() => { moved.current = true; }}>{topicOf(piece.topic).title}</a>
            </div>
            <div className="um-title">
              <h1 id="um-h" ref={headRef} tabIndex={-1}>{piece.title}</h1>
              {piece.preview && <PreviewTag/>}
              {piece.plugin && <span className="um-pill" title={doc.labels.plugin.means}>{doc.labels.plugin.text}</span>}
            </div>
            <div className="um-body"><UmBlocks blocks={piece.blocks} ctx={ctx}/></div>
            <div className="um-more">
              {piece.more && (
                <a href={ctx.docUrl(piece.more.target.replace(/^doc:/, ''))} target="_blank" rel="noopener noreferrer">
                  {piece.more.text} <Icon name="external-link" size={11}/>
                </a>
              )}
              {nextPiece && (
                <a className="um-next" href={`#manual/${nextPiece.id}`} onClick={() => { moved.current = true; }}>
                  {doc.labels.next}: {nextPiece.title}
                </a>
              )}
            </div>
          </article>
        )}
      </div>
    </React.Fragment>
  );
};

window.UserManualPage = UserManualPage;
// The checklist's rule, so a test can ask it directly instead of rendering
// the page and reading ticks off it (ChatDrafts.sourceLabel does the same).
window.UserManual = { checklistDone: umChecklistDone };
