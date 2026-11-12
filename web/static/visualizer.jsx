/* The Now Playing visualizer: MilkDrop presets, drawn by Butterchurn.
 *
 * The expanded player (player.jsx PlayerSheet) renders <NowPlayingVisualizer>
 * as its stage: the picture is the BACKDROP, and the sheet's panel (lyrics,
 * equalizer or queue) sits on top of it, see-through by the amount the
 * "panel" slider in the visualizer's bar sets (30-100 %, 80 % until moved,
 * remembered per browser). Hide (no panel) leaves the picture alone. With
 * the visualizer off, unsupported or casting, the panel is a plain card.
 * It analyses this browser's own
 * playback: the engine taps the player's AnalyserNode (player.jsx's Web
 * Audio graph, after the EQ and before the volume, so turning the volume
 * down doesn't dim the picture). Nothing in the graph changes: the engine
 * only adds its own analysers beside the destination.
 *
 * - Off (the default) is the bars, as before. The toggle is remembered per
 *   browser, like the preset and the lock.
 * - While the player casts to a room there is no audio here to see, so the
 *   section says so in one quiet line and offers no toggle.
 * - A browser without WebGL 2 (or WebAssembly) keeps the bars and says why.
 * - Previous / next preset are buttons in the visualizer's bar (the arrow
 *   keys too, while the picture itself has the focus - never while a field
 *   or a lyric line in the panel has it); the name shows for a moment. The
 *   halves of the picture are not buttons any more (2026-11-12): the panel
 *   covers them. Random, lock (presets change on their own every
 *   VIZ_AUTO_SEC unless locked), the preset list, the panel's opacity and
 *   full screen sit in the same bar, under the panel.
 * - Full screen (Fullscreen API on the stage; Esc exits, a double-click on
 *   the picture toggles) shows the picture alone, without the panel, and
 *   keeps the screen awake while it plays: VizWakeLock below.
 * - Draws only while it is on screen, the tab is visible and the music is
 *   playing; the pixel ratio is capped so a 4K screen doesn't melt.
 *
 * The engine (web/static/vendor/butterchurn, MIT) is an ES module, loaded
 * the first time the visualizer is turned on. It runs a preset's equations
 * as WebAssembly only (onlyUseWASM): no preset can run JavaScript here.
 * The standard presets are Domovoi's own (web/static/visualizer/presets);
 * the ones an admin adds come from /api/visualizer/presets
 * (web/backend/api/visualizer.py), the same for every browser in the house.
 *
 * Loaded via Babel; React.* hooks and viz-prefixed globals (shared scope,
 * see player.jsx).
 */

const VIZ_ON_KEY = 'domovoi-viz-milkdrop';
const VIZ_PRESET_KEY = 'domovoi-viz-preset';
const VIZ_LOCK_KEY = 'domovoi-viz-lock';
const VIZ_AUTO_SEC = 40;
const VIZ_BLEND_SEC = 2.5;
const VIZ_NAME_MS = 3000;
// The most pixels the engine draws: a 1080p frame. A 4K full screen is
// drawn at this size and scaled up by the browser.
const VIZ_MAX_PIXELS = 1920 * 1080;
const VIZ_MAX_RATIO = 1.5;
// The panel over the picture: how opaque, 0.3-1 (0.8 until moved). Never
// below 0.3, so the words on it never vanish into the picture.
const VIZ_PANEL_KEY = 'domovoi-viz-panel-opacity';
const VIZ_PANEL_MIN = 0.3;
const VIZ_PANEL_DEFAULT = 0.8;
const vizPanelOpacity = (raw) => {
  if (raw == null || String(raw).trim() === '') return VIZ_PANEL_DEFAULT;
  const n = Number(raw);
  if (!Number.isFinite(n)) return VIZ_PANEL_DEFAULT;
  return Math.max(VIZ_PANEL_MIN, Math.min(1, n));
};

const vizStore = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v == null ? fallback : v; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch { /* private mode: not remembered */ }
  },
};

/* WebGL 2 and WebAssembly, asked once; the probe's context is let go. */
const vizSupported = (() => {
  let ok = null;
  return () => {
    if (ok !== null) return ok;
    try {
      const c = document.createElement('canvas');
      const gl = c.getContext('webgl2');
      ok = !!gl && typeof WebAssembly === 'object';
      const lose = gl && gl.getExtension('WEBGL_lose_context');
      if (lose) lose.loseContext();
    } catch { ok = false; }
    return ok;
  };
})();

/* The engine, imported once. A plain dynamic import(): the module is
 * served from this dashboard like every vendored script. */
// A browser remembers a failed module fetch for the rest of the page's
// life (a load while the server was restarting after an update failed with
// "Failed to fetch dynamically imported module" until a reload,
// 2026-11-12), so each retry asks for the same file under a new query.
const _vizEngine = { p: null, tries: 0 };
const vizLoadEngine = () => {
  if (!_vizEngine.p) {
    // eslint-disable-next-line no-new-func
    const load = new Function('u', 'return import(u)');
    const url = '/vendor/butterchurn/butterchurn.min.js' + (_vizEngine.tries ? `?retry=${_vizEngine.tries}` : '');
    _vizEngine.tries += 1;
    _vizEngine.p = load(url)
      .then((m) => {
        const bc = m && (m.default || m);
        if (!bc || typeof bc.createVisualizer !== 'function') throw new Error('the visualizer engine did not load');
        return bc;
      })
      .catch((e) => { _vizEngine.p = null; throw e; });
  }
  return _vizEngine.p;
};

/* The preset list: the standard set (static, so a guest browser has it
 * too) then the house's added ones (a household read; quietly none when
 * this browser can't read them). Each entry: {key, name, source, ...}. */
const vizLoadCatalog = async () => {
  let standard = [];
  let defaultKey = null;
  try {
    const r = await fetch('/visualizer/presets/index.json', { credentials: 'same-origin' });
    const doc = await r.json();
    standard = (doc.presets || []).map((x) => ({
      key: `std:${x.id}`, name: x.name, source: 'standard', url: `/visualizer/presets/${x.file}`,
    }));
    if (doc.default) defaultKey = `std:${doc.default}`;
  } catch { /* none: the added ones may still be there */ }
  let added = [];
  let canRead = true;
  try {
    const body = await apiGet('/api/visualizer/presets', { quiet: true });
    added = ((body && body.presets) || []).map((x) => ({
      key: `add:${x.id}`, id: x.id, name: x.name, source: 'added', notes: x.notes || [],
    }));
  } catch { canRead = false; }
  return { list: standard.concat(added), defaultKey, canRead };
};

const _vizPresetCache = new Map();
const vizFetchPreset = async (entry) => {
  if (_vizPresetCache.has(entry.key)) return _vizPresetCache.get(entry.key);
  let preset;
  if (entry.source === 'standard') {
    const r = await fetch(entry.url, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(`preset ${entry.name}: ${r.status}`);
    preset = await r.json();
  } else {
    const body = await apiGet(`/api/visualizer/presets/${encodeURIComponent(entry.id)}`, { quiet: true });
    preset = body && body.preset;
  }
  if (!preset) throw new Error(`preset ${entry.name} is empty`);
  _vizPresetCache.set(entry.key, preset);
  return preset;
};

/* Keep the screen on while the visualizer plays full screen.
 *
 * 1. The Screen Wake Lock API where the browser offers it. It needs a
 *    secure context (https, or localhost), and the dashboard is usually
 *    plain http on the LAN (http://192.168.1.20:6369), where
 *    navigator.wakeLock is undefined. A held lock is released by the
 *    browser when the tab is hidden, so it is asked for again on return.
 * 2. Otherwise a muted, invisible <video> playing a MediaStream: a 16x16
 *    canvas's captureStream(), redrawn twice a second. Chromium holds a
 *    display wake lock for a playing video, and a MediaStream counts even
 *    muted (a muted FILE does not: measured 2026-11-11 in Chrome and Edge
 *    on Windows, plain http on a non-localhost name, by the system's
 *    display-required flag). Other browsers: docs/FAQ.md, "Does the music
 *    visualizer keep the screen on?".
 *
 * hold(true) while full screen + playing + visible; hold(false) otherwise.
 * mode() says which one holds: 'api', 'video' or null (for the manual
 * check and tests). */
const VizWakeLock = (() => {
  let want = false;
  let sentinel = null;
  let video = null;
  let canvas = null;
  let timer = 0;
  let mode = null;

  const apiAvailable = () => !!(window.isSecureContext && navigator.wakeLock
    && typeof navigator.wakeLock.request === 'function');

  const stopVideo = () => {
    if (timer) { clearInterval(timer); timer = 0; }
    if (video) {
      try { video.pause(); } catch { /* gone */ }
      const stream = video.srcObject;
      try { if (stream && stream.getTracks) stream.getTracks().forEach((t) => t.stop()); } catch { /* gone */ }
      video.srcObject = null;
      if (video.parentNode) video.parentNode.removeChild(video);
      video = null;
    }
    canvas = null;
  };

  const startVideo = () => {
    if (video) return true;
    canvas = document.createElement('canvas');
    if (typeof canvas.captureStream !== 'function') { canvas = null; return false; }
    canvas.width = 16; canvas.height = 16;
    const g = canvas.getContext('2d');
    let n = 0;
    const draw = () => { n ^= 1; g.fillStyle = n ? 'rgb(0,0,0)' : 'rgb(1,1,1)'; g.fillRect(0, 0, 16, 16); };
    draw();
    video = document.createElement('video');
    video.muted = true;
    video.defaultMuted = true;
    video.playsInline = true;
    video.setAttribute('playsinline', '');
    video.setAttribute('muted', '');
    video.setAttribute('aria-hidden', 'true');
    video.tabIndex = -1;
    video.className = 'viz-wake-video';
    video.srcObject = canvas.captureStream(2);
    document.body.appendChild(video);
    timer = setInterval(draw, 500);
    const pr = video.play();
    if (pr && pr.catch) pr.catch(() => { if (mode === 'video') { stopVideo(); mode = null; } });
    return true;
  };

  const acquire = async () => {
    if (apiAvailable()) {
      try {
        if (!sentinel) {
          const s = await navigator.wakeLock.request('screen');
          if (!want) { try { await s.release(); } catch { /* released */ } return; }
          sentinel = s;
          s.addEventListener('release', () => { if (sentinel === s) sentinel = null; });
        }
        mode = 'api';
        return;
      } catch { /* refused (battery saver, policy): try the video */ }
    }
    mode = startVideo() ? 'video' : null;
  };

  const releaseAll = () => {
    if (sentinel) { const s = sentinel; sentinel = null; try { s.release(); } catch { /* released */ } }
    stopVideo();
    mode = null;
  };

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && want && !sentinel && !video) acquire();
    });
  }

  return {
    hold(on) {
      if (on === want) return;
      want = on;
      if (on) acquire(); else releaseAll();
    },
    mode() { return mode; },
  };
})();

/* Full screen, with the prefixed WebKit spelling for Safari on a Mac.
 * iPhone Safari offers full screen for <video> only: no button there. */
const vizFs = {
  enabled() { return !!(document.fullscreenEnabled || document.webkitFullscreenEnabled); },
  element() { return document.fullscreenElement || document.webkitFullscreenElement || null; },
  enter(el) {
    const fn = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!fn) return Promise.resolve();
    try { return Promise.resolve(fn.call(el)).catch(() => {}); } catch { return Promise.resolve(); }
  },
  exit() {
    const fn = document.exitFullscreen || document.webkitExitFullscreen;
    if (!fn) return Promise.resolve();
    try { return Promise.resolve(fn.call(document)).catch(() => {}); } catch { return Promise.resolve(); }
  },
};

/* Read a picked file as text (presets are small text files). */
const vizReadFile = (file) => new Promise((resolve, reject) => {
  const fr = new FileReader();
  fr.onload = () => resolve(String(fr.result || ''));
  fr.onerror = () => reject(fr.error || new Error('could not read the file'));
  fr.readAsText(file);
});

/* The list of presets, with "add" and "remove" for an admin. */
const VizPresetMenu = ({ catalog, current, onPick, onClose, onChanged }) => {
  const admin = useAdminSignedIn();
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState('');
  // What the last upload said, shown here (a toast would sit outside a
  // full-screen visualizer, where nobody sees it).
  const [said, setSaid] = React.useState([]);
  const fileRef = React.useRef(null);
  const upload = async (files) => {
    setErr('');
    setSaid([]);
    setBusy(true);
    const notes = [];
    let added = 0;
    try {
      for (const f of Array.from(files || [])) {
        try {
          const content = await vizReadFile(f);
          const r = await apiPost('/api/visualizer/presets', { filename: f.name, content });
          added += 1;
          (r && r.notes || []).forEach((n) => notes.push(`${r.name}: ${n}`));
        } catch (e) {
          setErr(`${f.name}: ${typeof apiErrorText === 'function' ? apiErrorText(e, 0) : String(e)}`);
        }
      }
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
    if (added) {
      setSaid([added === 1 ? 'preset added' : `${added} presets added`].concat(notes));
      onChanged();
    }
  };
  const remove = async (entry) => {
    setErr('');
    try {
      await apiDelete(`/api/visualizer/presets/${encodeURIComponent(entry.id)}`);
      _vizPresetCache.delete(entry.key);
      onChanged();
    } catch (e) {
      setErr(typeof apiErrorText === 'function' ? apiErrorText(e, 0) : String(e));
    }
  };
  const standard = catalog.list.filter((x) => x.source === 'standard');
  const added = catalog.list.filter((x) => x.source === 'added');
  const row = (x) => (
    <div key={x.key} className={`viz-menu-row${x.key === current ? ' on' : ''}`}>
      <button type="button" className="viz-menu-pick" aria-current={x.key === current ? 'true' : undefined}
              onClick={() => onPick(x.key)}>
        {x.name}
      </button>
      {x.source === 'added' && admin && (
        <button type="button" className="btn btn-ghost btn-icon" title={`remove ${x.name}`}
                aria-label={`remove ${x.name}`} onClick={() => remove(x)}>
          <Icon name="trash-2" size={14}/>
        </button>
      )}
    </div>
  );
  return (
    <div className="viz-menu" role="dialog" aria-label="presets"
         onClick={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
      <div className="viz-menu-head">
        <span>presets</span>
        <button type="button" className="btn btn-ghost btn-icon" aria-label="close" onClick={onClose}>
          <Icon name="x" size={14}/>
        </button>
      </div>
      <div className="viz-menu-list">
        <div className="viz-menu-group">standard</div>
        {standard.map(row)}
        {added.length > 0 && <div className="viz-menu-group">added for this house</div>}
        {added.map(row)}
      </div>
      <div className="viz-menu-foot">
        {admin ? (
          <>
            <input ref={fileRef} type="file" accept=".milk,.json" multiple hidden
                   onChange={(e) => upload(e.target.files)}/>
            <Button icon="upload" disabled={busy} onClick={() => fileRef.current && fileRef.current.click()}>
              {busy ? 'adding…' : 'add presets'}
            </Button>
            <span className="viz-menu-note">
              .milk or Butterchurn .json · a .milk file's custom shaders aren't converted
            </span>
          </>
        ) : (
          <span className="viz-menu-note">
            {catalog.canRead ? 'an admin can add presets for the house' : 'presets added for the house show on paired browsers'}
          </span>
        )}
        {said.map((t, i) => <div key={i} className="viz-menu-said" role="status">{t}</div>)}
        {err && <div className="viz-menu-err" role="alert">{err}</div>}
      </div>
    </div>
  );
};

/* The MilkDrop picture itself, with `panel` (the expanded player's tab
 * content, or null for none) laid over it. */
const VizMilkdrop = ({ p, panel = null }) => {
  const stageRef = React.useRef(null);
  const canvasRef = React.useRef(null);
  const vizRef = React.useRef(null);
  const analyserRef = React.useRef(null);
  const rafRef = React.useRef(0);
  const nameTimer = React.useRef(0);
  const loadSeq = React.useRef(0);
  const [engineErr, setEngineErr] = React.useState('');
  const [engineTry, setEngineTry] = React.useState(0);
  const [ready, setReady] = React.useState(false);
  const [catalog, setCatalog] = React.useState({ list: [], defaultKey: null, canRead: true });
  const [current, setCurrent] = React.useState(() => vizStore.get(VIZ_PRESET_KEY, ''));
  const [showName, setShowName] = React.useState(false);
  const [locked, setLocked] = React.useState(() => vizStore.get(VIZ_LOCK_KEY, '0') === '1');
  const [menu, setMenu] = React.useState(false);
  const [onScreen, setOnScreen] = React.useState(true);
  const [docVisible, setDocVisible] = React.useState(() => document.visibilityState !== 'hidden');
  const [isFs, setIsFs] = React.useState(false);
  const [panelOpacity, setPanelOpacity] = React.useState(() => vizPanelOpacity(vizStore.get(VIZ_PANEL_KEY, null)));
  const changeOpacity = (v) => {
    const o = vizPanelOpacity(v);
    setPanelOpacity(o);
    vizStore.set(VIZ_PANEL_KEY, String(o));
  };

  const playing = p.status === 'playing';
  const analyser = p.getAnalyser ? p.getAnalyser() : null;

  // ── the catalogue ──────────────────────────────────────────────────
  const reloadCatalog = React.useCallback(() => {
    vizLoadCatalog().then(setCatalog);
  }, []);
  React.useEffect(() => { reloadCatalog(); }, [reloadCatalog]);

  // The preset on screen: the remembered one if it still exists, else the
  // set's default, else the first.
  const keys = catalog.list.map((x) => x.key);
  const shownKey = keys.includes(current) ? current
    : (catalog.defaultKey && keys.includes(catalog.defaultKey) ? catalog.defaultKey : keys[0] || '');
  const shownEntry = catalog.list.find((x) => x.key === shownKey) || null;

  // ── the engine, made once the graph exists ─────────────────────────
  React.useEffect(() => {
    if (vizRef.current || !analyser || !canvasRef.current) return undefined;
    let dead = false;
    vizLoadEngine().then((bc) => {
      if (dead || vizRef.current) return;
      const canvas = canvasRef.current;
      const w = Math.max(2, canvas.width), h = Math.max(2, canvas.height);
      const viz = bc.createVisualizer(analyser.context, canvas, {
        width: w, height: h, pixelRatio: 1, textureRatio: 1, onlyUseWASM: true,
      });
      viz.connectAudio(analyser);
      vizRef.current = viz;
      analyserRef.current = analyser;
      setEngineErr('');
      setReady(true);
    }).catch((e) => {
      if (dead) return;
      // Usually the server restarting (an update): try again a few times
      // on a fresh address before saying so for good.
      try { console.warn('visualizer engine load failed:', e); } catch { /* no console */ }
      if (engineTry < 3) {
        setEngineErr('retry');
        retryTimer = setTimeout(() => { if (!dead) setEngineTry((n) => n + 1); }, 3000);
      } else setEngineErr('fail');
    });
    let retryTimer = null;
    return () => { dead = true; if (retryTimer) clearTimeout(retryTimer); };
  }, [analyser, engineTry]);

  // Let it go with the component: our analysers off the graph, the GL
  // context back to the browser.
  React.useEffect(() => () => {
    cancelAnimationFrame(rafRef.current);
    clearTimeout(nameTimer.current);
    const viz = vizRef.current;
    vizRef.current = null;
    if (viz) {
      try { if (analyserRef.current) viz.disconnectAudio(analyserRef.current); } catch { /* already off */ }
      try { viz.loseGLContext(); } catch { /* gone */ }
    }
    VizWakeLock.hold(false);
  }, []);

  // ── load the preset on screen ──────────────────────────────────────
  const flashName = React.useCallback(() => {
    setShowName(true);
    clearTimeout(nameTimer.current);
    nameTimer.current = setTimeout(() => setShowName(false), VIZ_NAME_MS);
  }, []);
  React.useEffect(() => {
    if (!ready || !shownEntry) return;
    const seq = ++loadSeq.current;
    vizFetchPreset(shownEntry).then((preset) => {
      if (seq !== loadSeq.current || !vizRef.current) return;
      return vizRef.current.loadPreset(preset, VIZ_BLEND_SEC);
    }).catch(() => { /* a broken added preset: stay on the last one */ });
    flashName();
  }, [ready, shownKey]); // eslint-disable-line

  const pick = React.useCallback((key) => {
    setCurrent(key);
    vizStore.set(VIZ_PRESET_KEY, key);
  }, []);
  const step = React.useCallback((by) => {
    if (!keys.length) return;
    const i = Math.max(0, keys.indexOf(shownKey));
    pick(keys[(i + by + keys.length) % keys.length]);
  }, [keys.join('|'), shownKey, pick]); // eslint-disable-line
  const random = React.useCallback(() => {
    if (keys.length < 2) return;
    let k = shownKey;
    while (k === shownKey) k = keys[Math.floor(Math.random() * keys.length)];
    pick(k);
  }, [keys.join('|'), shownKey, pick]); // eslint-disable-line
  const toggleLock = () => {
    const next = !locked;
    setLocked(next);
    vizStore.set(VIZ_LOCK_KEY, next ? '1' : '0');
  };

  // ── on screen? tab visible? ────────────────────────────────────────
  React.useEffect(() => {
    const el = stageRef.current;
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const io = new IntersectionObserver((entries) => setOnScreen(entries.some((e) => e.isIntersecting)));
    io.observe(el);
    return () => io.disconnect();
  }, []);
  React.useEffect(() => {
    const on = () => setDocVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', on);
    return () => document.removeEventListener('visibilitychange', on);
  }, []);

  // ── size: the stage's box, at a capped pixel ratio ─────────────────
  React.useEffect(() => {
    const stage = stageRef.current;
    const canvas = canvasRef.current;
    if (!stage || !canvas) return undefined;
    const fit = () => {
      const r = stage.getBoundingClientRect();
      const cssW = Math.max(1, r.width), cssH = Math.max(1, r.height);
      let ratio = Math.min(window.devicePixelRatio || 1, VIZ_MAX_RATIO);
      if (cssW * cssH * ratio * ratio > VIZ_MAX_PIXELS) ratio = Math.sqrt(VIZ_MAX_PIXELS / (cssW * cssH));
      const w = Math.max(2, Math.round(cssW * ratio)), h = Math.max(2, Math.round(cssH * ratio));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w; canvas.height = h;
        if (vizRef.current) { try { vizRef.current.setRendererSize(w, h); } catch { /* resized next frame */ } }
      }
    };
    fit();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', fit);
      return () => window.removeEventListener('resize', fit);
    }
    const ro = new ResizeObserver(fit);
    ro.observe(stage);
    return () => ro.disconnect();
  }, [ready]);

  // ── draw only while it can be seen and the music plays ─────────────
  const running = ready && playing && docVisible && (onScreen || isFs);
  React.useEffect(() => {
    if (!running) return undefined;
    const frame = () => {
      const viz = vizRef.current;
      if (viz) { try { viz.render(); } catch { /* a frame lost; keep going */ } }
      rafRef.current = requestAnimationFrame(frame);
    };
    rafRef.current = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(rafRef.current);
  }, [running]);

  // ── presets change on their own unless locked ──────────────────────
  React.useEffect(() => {
    if (!running || locked || keys.length < 2) return undefined;
    const t = setInterval(random, VIZ_AUTO_SEC * 1000);
    return () => clearInterval(t);
  }, [running, locked, random, keys.length]);

  // ── full screen and the screen staying awake ───────────────────────
  React.useEffect(() => {
    const on = () => setIsFs(vizFs.element() === stageRef.current && !!stageRef.current);
    document.addEventListener('fullscreenchange', on);
    document.addEventListener('webkitfullscreenchange', on);
    return () => {
      document.removeEventListener('fullscreenchange', on);
      document.removeEventListener('webkitfullscreenchange', on);
    };
  }, []);
  React.useEffect(() => {
    VizWakeLock.hold(isFs && playing && docVisible);
  }, [isFs, playing, docVisible]);
  const toggleFs = () => {
    if (isFs) vizFs.exit();
    else if (stageRef.current) vizFs.enter(stageRef.current);
  };

  // ── a double-click on the picture (not the panel) is full screen ────
  const onPicture = (e) => !!e && (e.target === stageRef.current || e.target === canvasRef.current);
  const dbl = (e) => {
    if (!onPicture(e)) return;
    e.stopPropagation();
    if (vizFs.enabled()) toggleFs();
  };
  // Keys only while the picture itself has the focus: in the panel the
  // arrows belong to a slider, a lyric line or the queue's name field.
  const onKey = (e) => {
    if (e.key === 'Escape' && menu) {
      // The preset list closes; the sheet around it stays open.
      e.preventDefault();
      e.stopPropagation();
      setMenu(false);
      return;
    }
    if (e.target !== stageRef.current) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      e.stopPropagation();
      step(e.key === 'ArrowLeft' ? -1 : 1);
    } else if (e.key === 'f' || e.key === 'F') {
      if (vizFs.enabled()) { e.preventDefault(); e.stopPropagation(); toggleFs(); }
    }
  };

  const waiting = !analyser
    ? 'starts when music plays in this browser'
    : engineErr === 'retry' ? "the visualizer couldn't load yet — trying again…"
    : engineErr === 'fail' ? "the visualizer couldn't load. Reload the page, or turn it off and on, to try again."
    : (!ready ? 'loading…' : '');

  const showPanel = panel != null && !isFs;
  return (
    <div ref={stageRef} className={`viz-stage${isFs ? ' fs' : ''}${showPanel ? ' with-panel' : ''}`} tabIndex={0}
         role="group" aria-label="visualizer" aria-roledescription="visualizer"
         onKeyDown={onKey} onDoubleClick={dbl}>
      <canvas ref={canvasRef} className="viz-canvas" aria-hidden="true"/>
      {showPanel && <div className="viz-over" style={{ opacity: panelOpacity }}>{panel}</div>}
      <div className="viz-ctl" onDoubleClick={(e) => e.stopPropagation()}>
        {(waiting || shownEntry) && (
          <div className={`viz-name${showName || waiting ? ' show' : ''}`} aria-live="polite">
            {waiting || shownEntry.name}
          </div>
        )}
        {ready && (
          <div className="viz-bar">
            <button type="button" className="btn btn-ghost btn-icon" title="previous preset" aria-label="previous preset"
                    onClick={() => step(-1)}>
              <Icon name="skip-back" size={14}/>
            </button>
            <button type="button" className="btn btn-ghost btn-icon" title="next preset" aria-label="next preset"
                    onClick={() => step(1)}>
              <Icon name="skip-forward" size={14}/>
            </button>
            <button type="button" className="btn btn-ghost btn-icon" title="random preset" aria-label="random preset"
                    onClick={random}>
              <Icon name="shuffle" size={14}/>
            </button>
            <button type="button" className="btn btn-ghost btn-icon" aria-pressed={locked}
                    title={locked ? 'locked on this preset' : `presets change every ${VIZ_AUTO_SEC}s`}
                    aria-label={locked ? 'unlock preset' : 'lock this preset'} onClick={toggleLock}>
              <Icon name={locked ? 'lock' : 'lock-open'} size={14}/>
            </button>
            <button type="button" className="btn btn-ghost btn-icon" title="presets" aria-label="presets"
                    aria-expanded={menu} onClick={() => setMenu(!menu)}>
              <Icon name="list-music" size={14}/>
            </button>
            {showPanel && (
              <label className="viz-opacity" title="how much the panel covers the picture">
                <Icon name="layers" size={14}/>
                <input type="range" min={VIZ_PANEL_MIN * 100} max={100} step={5}
                       value={Math.round(panelOpacity * 100)} aria-label="panel opacity"
                       aria-valuetext={`${Math.round(panelOpacity * 100)}%`}
                       onChange={(e) => changeOpacity(Number(e.target.value) / 100)}/>
              </label>
            )}
            {vizFs.enabled() && (
              <button type="button" className="btn btn-ghost btn-icon"
                      title={isFs ? 'exit full screen' : 'full screen'}
                      aria-label={isFs ? 'exit full screen' : 'full screen'} onClick={toggleFs}>
                <Icon name={isFs ? 'minimize-2' : 'maximize-2'} size={14}/>
              </button>
            )}
          </div>
        )}
      </div>
      {menu && (
        <VizPresetMenu catalog={catalog} current={shownKey}
                       onPick={(k) => { pick(k); }} onClose={() => setMenu(false)}
                       onChanged={reloadCatalog}/>
      )}
    </div>
  );
};

/* The expanded player's stage: its head (`head`: the sheet's tabs, then the
 * MilkDrop toggle), then MilkDrop with `panel` over it - or, with MilkDrop
 * off, unsupported or casting, the bars (not while casting) and `panel` as
 * a plain card. `panel` null: no panel (the sheet's Hide). */
const NowPlayingVisualizer = ({ p, remote, head = null, panel = null }) => {
  const [on, setOn] = React.useState(() => vizStore.get(VIZ_ON_KEY, '0') === '1');
  const plain = (note, bars) => (
    <div className="viz-section">
      <div className="viz-head">{head}{note}</div>
      {bars}
      {panel != null && <div className="viz-solid">{panel}</div>}
    </div>
  );
  if (remote) {
    return plain(<div className="viz-note">visualizer shows when playing in this browser</div>, null);
  }
  const supported = vizSupported();
  const toggle = (next) => { setOn(next); vizStore.set(VIZ_ON_KEY, next ? '1' : '0'); };
  const control = supported ? (
    <label className="viz-toggle">
      <input type="checkbox" checked={on} onChange={(e) => toggle(e.target.checked)}/>
      milkdrop visualizer
    </label>
  ) : (
    <span className="viz-note-inline">milkdrop visualizer needs WebGL 2 in this browser</span>
  );
  if (!(supported && on)) return plain(control, <div className="viz-bars"><Visualizer p={p} height={64}/></div>);
  return (
    <div className="viz-section">
      <div className="viz-head">{head}{control}</div>
      <VizMilkdrop p={p} panel={panel}/>
    </div>
  );
};

Object.assign(window, { NowPlayingVisualizer, VizWakeLock, vizPanelOpacity });
