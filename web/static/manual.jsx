/* User Manual + About — the "how domovoi works" page.
 *
 * A cute, interactive topology of the whole system: hover a node (desktop) to
 * trace its wiring + scramble its name, scroll it into view (mobile) to reveal
 * it, and click any node to open a 5-tab manual (features · tech stack ·
 * troubleshooting · faq · how to). `tech stack` and `how to` are node-scoped:
 * clicking a node deep-links into Tech Stack at its #um-tech-<id> row with a
 * flash-highlight, and How To reflects the piece you clicked.
 *
 * This page is reached from the About tab in Settings (route "#manual"); it is
 * NOT a workspace sidebar item.
 *
 * Ported from docs/prototypes/user_manual_prototype.html — the source of truth
 * for layout, animation and copy. Shared primitives (Card, Tabs, Pill, Button,
 * Icon, StatusDot) replace the prototype's inline CSS; the SVG-wire +
 * requestAnimationFrame idle-float / wire-redraw logic stays bespoke (there is
 * no primitive for it). Amber-only accent, no emoji (unicode glyphs), JetBrains
 * Mono for code-shaped text, ≤300ms animations, honors prefers-reduced-motion.
 */

/* ── node topology ─────────────────────────────────────────────── */
const UM_ROWS = [
  ['satellites', 'web'],
  ['domovoi'],
  ['whisper', 'ollama', 'tts'],
  ['postgres', 'mpd', 'workers'],
];

const UM_NODES = {
  satellites:   { glyph: '◈', name: 'satellites',   role: 'the ears & mouth in each room',    tech: 'pi zero 2 w · mic · aux',       dot: 'idle' },
  web:          { glyph: '▦', name: 'web',          role: 'this dashboard',                   tech: 'fastapi :6369 · react' },
  domovoi: { glyph: '▚', name: 'domovoi', role: 'the brain — routes every request', tech: 'fastapi · on domovoi · 20 handlers', dot: 'live', hub: true },
  whisper:      { glyph: '◟', name: 'whisper',      role: 'turns your speech into text',      tech: 'faster-whisper · cuda' },
  ollama:       { glyph: '◍', name: 'ollama',       role: 'understands & answers',            tech: 'llama3.2 · qwen2.5' },
  tts:          { glyph: '◠', name: 'tts',          role: 'gives Domovoi his voice',          tech: 'edge → piper → system' },
  postgres:     { glyph: '▤', name: 'postgres',     role: 'remembers everything',             tech: 'postgres 16 · flyway' },
  mpd:          { glyph: '♪', name: 'mpd',          role: 'plays music, per room',            tech: 'one container / room' },
  workers:      { glyph: '⚙', name: 'workers',      role: 'background jobs',                  tech: 'core loops · plugin workers' },
};

const UM_EDGES = [
  ['satellites', 'domovoi'],
  ['domovoi', 'whisper'], ['domovoi', 'ollama'], ['domovoi', 'tts'],
  ['domovoi', 'postgres'], ['domovoi', 'mpd'], ['domovoi', 'workers'],
  ['web', 'domovoi'], ['web', 'postgres'],
];

const UM_TITLES = {
  satellites: 'satellites', domovoi: 'the Domovoi server', whisper: 'whisper (speech-to-text)',
  ollama: 'ollama (the language model)', tts: 'text-to-speech', postgres: 'postgres (state)',
  mpd: 'music playback', workers: 'background workers', web: 'the web dashboard',
};

/* node-scoped how-to: what you can DO with this part, and how to DIAGNOSE it. */
const UM_HOWTO = {
  satellites: {
    act: ['Add one: flash the Pi and drop in its config — it shows up here once it connects.',
          'Change its voice or wake word in Settings, then push to the room.',
          'Set playback volume per room from the Music page.'],
    diag: ['No response? Check the wake loop is running and the mic board matches its trained model.',
           'Choppy audio? Check wi-fi rx rate (iw dev wlan0 link); reassociate if stuck at 1 Mbit/s.',
           "No sound? Make sure nothing else is holding the Pi's single audio card."] },
  web: {
    act: ['Switch pages from the left nav — each surface manages one area.',
          'Change domovoi settings under the gear → Configuration.',
          'Check versions / pull updates under Configuration → Version.'],
    diag: ['Stale data? Hard-refresh (Ctrl+Shift+R).',
           'Actions failing? The dashboard must reach the Domovoi server on :6370 — confirm it is running.'] },
  domovoi: {
    act: ['Restart it on the Domovoi host to apply restart-tier config changes.',
          'Check health at /v1/health and connectivity at /v1/connectivity.',
          'Pull latest under Configuration → Version, then bounce it by hand.'],
    diag: ['Nothing responds? Confirm the process is up and Postgres is reachable.',
           'Bad routing? Make sure both Ollama models are installed and loaded.'] },
  whisper: {
    act: ['Switch the speech-to-text model or pre-download a size from the Models page.',
          'Prefer large-v3 for accuracy; a smaller size if VRAM is tight.'],
    diag: ['Slow or garbled? Confirm it is on CUDA (float16), not the CPU.',
           'First use slow? The model may be cold-downloading — pre-stage it on the Models page.'] },
  ollama: {
    act: ['Switch the Q&A or tool-routing model, or pull a new one, from the Models page.',
          'Two models by design: a fast one answers, a stronger one routes tools.'],
    diag: ['LLM commands failing? Check the Ollama server is reachable and the model is installed.',
           "See what's loaded in VRAM right now on the hardware panel."] },
  tts: {
    act: ['Pick or upload a voice under Settings → Voices and set a default.',
          'Register an Edge cloud voice by id, or upload a local Piper .onnx.'],
    diag: ['No voice? Play a sample — the engine falls back edge → piper → system.',
           'Wrong voice? Confirm the intended one is set as default.'] },
  postgres: {
    act: ['Mostly hands-off — schema changes are Flyway migrations applied on deploy.'],
    diag: ['State missing or errors? Confirm the Postgres container is up and reachable.',
           'Check the latest V### migration has been applied to both prod and test DBs.'] },
  mpd: {
    act: ['Play, queue and favorite music from the Music page — each room is independent.'],
    diag: ["'port not listening' usually just means nothing is playing (it binds lazily).",
           "No music in a room? Check that room's MPD container is running."] },
  workers: {
    act: ['Enable/disable background workers (acquisitions, memory, plugin workers…) under Configuration.'],
    diag: ["A feature isn't updating? Check its worker's enabled flag — and that the Domovoi server was restarted after the change."] },
};

/* ── scoped styles (bespoke topology + detail rows) ────────────── */
const UM_CSS = `
.um-wrap { max-width: 1080px; margin: 0 auto; }

.um-hero { margin: 8px 0; }
.um-hero h1 { margin: 6px 0; }
.um-hero p { max-width: 560px; color: var(--fg-muted); font-size: 15px; }

.um-topo { position: relative; margin: 24px 0 8px; padding: 8px; }
.um-wires { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 0; overflow: visible; }
.um-wire { fill: none; stroke: var(--border-strong); stroke-width: 1.4; stroke-dasharray: 4 4; opacity: 0.7; }
.um-wire.flow { stroke: var(--brand); stroke-width: 1.8; opacity: 1; stroke-dasharray: 6 6; animation: um-wire-flow 0.6s linear infinite; }
@keyframes um-wire-flow { to { stroke-dashoffset: -24; } }

.um-rows { position: relative; z-index: 1; display: flex; flex-direction: column; gap: 44px; align-items: center; }
.um-row { display: flex; gap: 26px; justify-content: center; flex-wrap: wrap; }

.um-node { position: relative; width: 190px; background: var(--card); border: 1px solid var(--border);
  border-radius: var(--r-md); padding: 12px 14px; cursor: pointer; text-align: left; box-shadow: var(--shadow-sm);
  transition: transform 180ms var(--ease), border-color 120ms, background 120ms, box-shadow 120ms; will-change: transform; }
.um-node:hover, .um-node.reveal { border-color: var(--brand); background: var(--brand-soft); box-shadow: var(--shadow-md); }
.um-node.hub { width: 230px; border-color: var(--border-strong); }
.um-nhead { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.um-glyph { font-family: var(--ff-mono); color: var(--brand); font-size: 13px; }
.um-name { font-family: var(--ff-mono); font-weight: 600; font-size: 13px; letter-spacing: 0.01em; }
.um-role { font-size: 12px; color: var(--fg-muted); }
.um-tech { font-family: var(--ff-mono); font-size: 11px; color: var(--fg-faint); margin-top: 6px; }
.um-ndot { margin-left: auto; display: inline-flex; }

@keyframes um-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(var(--um-fl, -5px)); } }
.um-node.floating { animation: um-float var(--um-dur, 6s) ease-in-out infinite; animation-delay: var(--um-delay, 0s); }

.um-hint { text-align: center; color: var(--fg-faint); font-size: 12px; margin-top: 26px; }
.um-hint .mono { color: var(--fg-subtle); }

.um-details-head { margin-top: 8px; }

.um-rowitem { display: flex; gap: 12px; padding: 11px 16px; border-bottom: 1px solid var(--border-soft); }
.um-rowitem:last-child { border-bottom: none; }
.um-rowitem .k { font-family: var(--ff-mono); font-size: 12px; font-weight: 600; min-width: 120px; color: var(--fg); }
.um-rowitem .v { font-size: 13px; color: var(--fg-muted); }
.um-rowitem .v .mono { color: var(--fg-subtle); }
.um-rowitem.flash { animation: um-flash 1.4s var(--ease); }
@keyframes um-flash { 0% { background: var(--brand-soft); } 100% { background: transparent; } }
.um-rowitem .k.diag { color: var(--warn); }
.um-rowitem .v ul { margin: 2px 0 0; padding-left: 16px; }
.um-rowitem .v li { margin: 3px 0; color: var(--fg-muted); }
.um-q { font-weight: 600; font-size: 14px; margin-bottom: 4px; color: var(--fg); }
.um-a { font-size: 13px; color: var(--fg-muted); max-width: 680px; }
.um-tcode { font-family: var(--ff-mono); font-size: 12px; background: var(--sunken); border: 1px solid var(--border-soft);
  border-radius: 4px; padding: 1px 5px; color: var(--fg); }

@media (max-width: 640px) {
  .um-node, .um-node.hub { width: 100%; max-width: 340px; }
  .um-row { gap: 18px; } .um-rows { gap: 30px; }
  .um-hero h1 { font-size: 24px; } .um-hero p { font-size: 13px; }
  .um-rowitem { flex-direction: column; gap: 3px; } .um-rowitem .k { min-width: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .um-node.floating { animation: none; }
  .um-wire.flow { animation: none; }
  .um-node { transition: none; }
}
`;

const UM_TABS = [
  { id: 'features', label: 'features' },
  { id: 'tech', label: 'tech stack' },
  { id: 'trouble', label: 'troubleshooting' },
  { id: 'faq', label: 'faq' },
  { id: 'howto', label: 'how to' },
];

/* two-column detail row (k / v), used across the panels */
const UMRow = ({ k, kClass, children }) => (
  <div className="um-rowitem">
    <div className={'k' + (kClass ? ' ' + kClass : '')}>{k}</div>
    <div className="v">{children}</div>
  </div>
);

const UMList = ({ items }) => (
  <ul>{items.map((i, ix) => <li key={ix}>{i}</li>)}</ul>
);

/* ── tech-stack rows (each anchored for node deep-linking) ─────── */
const UM_TECH = [
  { id: 'satellites', k: 'satellites', v: <>Pi Zero 2 W · openWakeWord · ReSpeaker 2-Mics HAT or XVF3800 USB array · <span className="mono">WebSocket</span> audio streaming · aux-out playback</> },
  { id: 'domovoi', k: 'domovoi', v: <>FastAPI · Python 3.11+ · native on Windows 11 for CUDA · intent router → 20 handlers · 11 background workers</> },
  { id: 'whisper', k: 'whisper', v: <>faster-whisper <span className="mono">large-v3</span> on CUDA (float16), via CTranslate2</> },
  { id: 'ollama', k: 'ollama', v: <><span className="mono">llama3.2:3b</span> answers questions · <span className="mono">qwen2.5:14b</span> routes tool calls (stronger schema adherence)</> },
  { id: 'tts', k: 'tts', v: <>engine chain: edge-tts (cloud neural) → Piper (local) → system voice · voices configurable per satellite</> },
  { id: 'postgres', k: 'postgres', v: <>PostgreSQL 16 (the only Dockerized piece) · Flyway migrations · <span className="mono">LISTEN/NOTIFY</span> live state bus</> },
  { id: 'mpd', k: 'mpd', v: <>Music Player Daemon — one lazily-spawned container per room · media-provider plugins fetch tracks into the library</> },
  { id: 'workers', k: 'workers', v: <>timer watcher, acquisition queue, library index/enrich, memory extractor, plus whatever workers your plugins register</> },
  { id: 'web', k: 'web', v: <>separate FastAPI on <span className="mono">:6369</span> · React (Babel-in-browser) · reads the same Postgres, calls the Domovoi server for live state</> },
];

/* ── live feature table (from /api/capabilities/manual, §8) ────── */
const _NET_PILL = {
  no:       <Pill tone="ok">offline</Pill>,
  degraded: <Pill tone="idle">degrades offline</Pill>,
  yes:      <Pill tone="warn">needs net</Pill>,
};

const UMFeaturesPanel = () => {
  const { data, loading } = useApiObject('/api/capabilities/manual');
  const handlers = (data && data.handlers) || [];
  return (
    <Card title="what Domovoi can do"
          sub="every capability is a voice handler — core and installed plugins alike; most work with no internet at all.">
      {data && data.stale && (
        <div className="meta" style={{ padding: '8px 16px', color: 'var(--warn)' }}>
          the core service isn't reachable — showing the last known feature set
        </div>
      )}
      {loading && handlers.length === 0 ? (
        <div style={{ padding: 24, fontSize: 12, color: 'var(--fg-muted)' }}>loading…</div>
      ) : handlers.map((h) => (
        <UMRow key={h.name} k={(h.display && h.display.label) || h.name}>
          {(h.example_phrases || []).length > 0
            ? <>say: {h.example_phrases.map((p, i) => (
                <span key={i} className="mono" style={{ marginRight: 6 }}>“{p}”</span>
              ))}</>
            : <span style={{ color: 'var(--fg-faint)' }}>—</span>}
          {' '}
          {h.origin && h.origin !== 'core' && <Pill tone="idle">plugin: {h.origin}</Pill>}
          {' '}
          {_NET_PILL[h.requires_network] || null}
        </UMRow>
      ))}
    </Card>
  );
};

/* ── the page ──────────────────────────────────────────────────── */
const UM_GLY = 'ABCDEFGHKMNPRSTVXZ0123456789#%&*/<>';

const UserManualPage = () => {
  const [view, setView] = React.useState('map');       // 'map' | 'details'
  const [tab, setTab] = React.useState('features');
  const [activeNode, setActiveNode] = React.useState(null);
  const [flashId, setFlashId] = React.useState(null);
  const [reduce] = React.useState(
    () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  const nodeRefs = React.useRef({});   // id → node DOM el
  const pathRefs = React.useRef([]);   // edge index → <path> DOM el
  const topoRef = React.useRef(null);

  /* imperative hover helpers (no setState → no re-render mid-hover) */
  const setFlow = (id, on) => {
    UM_EDGES.forEach((e, i) => {
      const p = pathRefs.current[i];
      if (p && (e[0] === id || e[1] === id)) p.classList.toggle('flow', on);
    });
  };
  const scramble = (id) => {
    if (reduce) return;
    const node = nodeRefs.current[id];
    const el = node && node.querySelector('.um-name');
    if (!el) return;
    const final = el.dataset.final || el.textContent;
    el.dataset.final = final;
    let frame = 0; const steps = 8;
    clearInterval(el._t);
    el._t = setInterval(() => {
      let out = '';
      for (let i = 0; i < final.length; i++) {
        out += i < (frame / steps) * final.length ? final[i] : UM_GLY[Math.floor(Math.random() * UM_GLY.length)];
      }
      el.textContent = out;
      if (++frame > steps) { clearInterval(el._t); el.textContent = final; }
    }, 24);
  };

  const openDetails = (id) => {
    setActiveNode(id);
    setTab('tech');   // nodes are tech components → land on the tech-stack tab
    setView('details');
  };

  /* topology: idle float + per-frame wire redraw + mobile scroll-reveal */
  React.useEffect(() => {
    if (view !== 'map') return;
    const nodes = nodeRefs.current;
    const topo = topoRef.current;
    if (!topo) return;

    const center = (el) => {
      const r = el.getBoundingClientRect();
      const t = topo.getBoundingClientRect();
      return { x: r.left - t.left + r.width / 2, y: r.top - t.top + r.height / 2 };
    };
    const drawWires = () => {
      UM_EDGES.forEach((e, i) => {
        const p = pathRefs.current[i];
        const na = nodes[e[0]], nb = nodes[e[1]];
        if (!p || !na || !nb) return;
        const a = center(na), b = center(nb);
        const my = (a.y + b.y) / 2;
        p.setAttribute('d', 'M' + a.x + ' ' + a.y + ' C ' + a.x + ' ' + my + ' ' + b.x + ' ' + my + ' ' + b.x + ' ' + b.y);
      });
    };

    // stagger each node so the whole graph drifts
    if (!reduce) {
      Object.keys(nodes).forEach((id, i) => {
        const n = nodes[id];
        if (!n) return;
        n.classList.add('floating');
        n.style.setProperty('--um-dur', (5.5 + (i % 4) * 0.9) + 's');
        n.style.setProperty('--um-delay', (-i * 0.7) + 's');
        n.style.setProperty('--um-fl', (i % 2 ? -5 : -7) + 'px');
      });
    }

    drawWires();
    let raf = 0;
    const loop = () => { drawWires(); raf = requestAnimationFrame(loop); };
    if (!reduce) raf = requestAnimationFrame(loop);
    const onResize = () => drawWires();
    window.addEventListener('resize', onResize);

    // mobile: reveal + trace each node once as it scrolls into view
    let io = null;
    if ('IntersectionObserver' in window && window.matchMedia('(max-width:640px)').matches) {
      io = new IntersectionObserver((entries) => {
        entries.forEach((en) => {
          if (!en.isIntersecting) return;
          const id = en.target.dataset.id;
          en.target.classList.add('reveal');
          scramble(id);
          setFlow(id, true);
          setTimeout(() => { en.target.classList.remove('reveal'); setFlow(id, false); }, 900);
          io.unobserve(en.target);
        });
      }, { threshold: 0.6 });
      Object.keys(nodes).forEach((id) => { if (nodes[id]) io.observe(nodes[id]); });
    }

    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      if (io) io.disconnect();
      Object.keys(nodes).forEach((id) => { if (nodes[id]) nodes[id].classList.remove('floating'); });
    };
  }, [view, reduce]);

  /* deep-link: scroll to the clicked node's tech row + flash it */
  React.useEffect(() => {
    if (view !== 'details' || tab !== 'tech' || !activeNode) return;
    const row = document.getElementById('um-tech-' + activeNode);
    if (row) row.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' });
    setFlashId(activeNode);
    const t = setTimeout(() => setFlashId(null), 1500);
    return () => clearTimeout(t);
  }, [view, tab, activeNode, reduce]);

  /* ── cover / topology ── */
  const cover = (
    <section>
      <div className="um-hero">
        <div className="eyebrow">the whole system, at a glance</div>
        <h1>how domovoi works</h1>
        <p>a Pi in each room hears you, Domovoi does the thinking, and the answer plays back through the
          speakers. hover a piece to see it light up — click any piece to open the manual.</p>
      </div>

      <div className="um-topo" ref={topoRef}>
        <svg className="um-wires">
          {UM_EDGES.map((e, i) => (
            <path key={i} className="um-wire" ref={(el) => { pathRefs.current[i] = el; }}/>
          ))}
        </svg>
        <div className="um-rows">
          {UM_ROWS.map((row, ri) => (
            <div className="um-row" key={ri}>
              {row.map((id) => {
                const n = UM_NODES[id];
                return (
                  <div key={id} data-id={id} className={'um-node' + (n.hub ? ' hub' : '')}
                       ref={(el) => { nodeRefs.current[id] = el; }}
                       onMouseEnter={() => { setFlow(id, true); scramble(id); }}
                       onMouseLeave={() => setFlow(id, false)}
                       onClick={() => openDetails(id)}>
                    <div className="um-nhead">
                      <span className="um-glyph">{n.glyph}</span>
                      <span className="um-name">{n.name}</span>
                      {n.dot === 'live' && <span className="um-ndot"><StatusDot tone="ok" live={!reduce}/></span>}
                      {n.dot === 'idle' && <span className="um-ndot"><StatusDot tone="idle"/></span>}
                    </div>
                    <div className="um-role">{n.role}</div>
                    <div className="um-tech">{n.tech}</div>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>

      <div className="um-hint">
        hover a node to trace its wiring · on a phone, scroll and each piece lights up as it appears ·{' '}
        <span className="mono">click → manual</span>
      </div>
    </section>
  );

  /* ── details panels ── */
  /* The feature table renders LIVE from /api/capabilities/manual (§8):
   * the merged handler registry — core AND installed plugins — with
   * origin, offline behavior, and example phrases straight from each
   * handler's tool schema. Kills the stale-docs failure mode: install
   * a plugin and its voice features appear here without a docs edit.
   * `stale: true` (core down, disk-cached copy) renders a notice. */
  const featuresPanel = <UMFeaturesPanel/>;

  const techPanel = (
    <Card title="what it's built on"
          sub="runs entirely on your own hardware — Domovoi, an i9 / 128 GB / triple-4070-Ti-Super box.">
      {UM_TECH.map((t) => (
        <div key={t.id} id={'um-tech-' + t.id} className={'um-rowitem' + (flashId === t.id ? ' flash' : '')}>
          <div className="k">{t.k}</div>
          <div className="v">{t.v}</div>
        </div>
      ))}
    </Card>
  );

  const troublePanel = (
    <Card title="when something's off" sub="the fixes that actually come up on this system, most-common first.">
      <UMRow k="choppy audio">check the Pi's wi-fi rate first: <span className="um-tcode">iw dev wlan0 link</span> — if rx is stuck at 1 Mbit/s, run <span className="um-tcode">wpa_cli reassociate</span> before touching anything else.</UMRow>
      <UMRow k="no sound at all">only one process can hold a Pi's audio card at a time (no software mixer on Pi OS Lite). Make sure nothing else is playing.</UMRow>
      <UMRow k="mpg123 crashes">on a fresh Pi it defaults to JACK and segfaults — force ALSA with <span className="um-tcode">-o alsa</span>.</UMRow>
      <UMRow k={'"port not listening"'}>the music stream binds lazily — that message usually just means nothing is playing yet.</UMRow>
      <UMRow k="one Pi worse than others">a hotter-running satellite drops mic frames — suspect its SD card / dependencies, not the code.</UMRow>
      <UMRow k="quiet / one-sided music">the XVF3800's audio-out is mono — use a mono→stereo adapter for a stereo speaker pair.</UMRow>
      <UMRow k={'"managed by your organization"'}>that Windows firewall popup is usually stale block rules, not a real policy.</UMRow>
      <UMRow k="domovoi can't reach a Pi">use a LAN hostname, never <span className="um-tcode">localhost</span> — on the Pi that resolves back to itself.</UMRow>
    </Card>
  );

  const faqItems = [
    ["Why is it called Domovoi?", 'A domovoi is a household guardian spirit from Slavic folklore, often taking the shape of a cat — hence the mascot. The name you say out loud to wake it is configurable.'],
    ['Does it work without internet?', "Yes — it's local-first. Speech-to-text, understanding, local voices, the music library, timers, intercom and more all run offline. Only web search, media-provider plugins and cloud voices need the network, and they degrade gracefully instead of breaking."],
    ['How do I add features?', 'Install plugins from the Plugins page — upload a zip or point at a GitHub repo. Plugins can add voice commands, background workers, dashboard pages and Android screens. Only install plugins from publishers you trust: they run with full access to your server.'],
    ['Is my data private?', 'Everything runs on your own hardware. There’s no cloud account and nothing leaves the house unless a network feature asks for it.'],
    ['Can I change the wake word?', 'Yes. Record a handful of clips on a satellite and train a custom model — Settings → Wake Words. (Training is Linux-only, so it runs off-box.)'],
    ['Can I add my own voice?', <>Upload a Piper <span className="mono">.onnx</span> model for a fully-local voice, or register a Microsoft Edge neural voice — Settings → Voices.</>],
    ['Why two different LLMs?', 'A small fast model handles open questions; a stronger model routes tool calls, where strict schema adherence matters more than speed.'],
    ['How do rooms play different music?', 'Each room gets its own music daemon, so the kitchen and the garage can play completely independent tracks at the same time.'],
  ];
  const faqPanel = (
    <Card title="frequently asked">
      {faqItems.map(([q, a], i) => (
        <div key={i} className="um-rowitem">
          <div className="v">
            <div className="um-q">{q}</div>
            <div className="um-a">{a}</div>
          </div>
        </div>
      ))}
    </Card>
  );

  const howto = UM_HOWTO[activeNode] || { act: [], diag: [] };
  const howtoPanel = (
    <Card title={'how to · ' + (UM_TITLES[activeNode] || 'the manual')}
          sub="common things you can do with this part — and how to check it when something is off.">
      {howto.act && howto.act.length > 0 && (
        <UMRow k="you can"><UMList items={howto.act}/></UMRow>
      )}
      <UMRow k="diagnose" kClass="diag"><UMList items={howto.diag}/></UMRow>
    </Card>
  );

  const panels = {
    features: featuresPanel, tech: techPanel, trouble: troublePanel, faq: faqPanel, howto: howtoPanel,
  };

  const details = (
    <section>
      <div className="um-details-head">
        <Button variant="ghost" icon="arrow-left" onClick={() => setView('map')}>back to map</Button>
      </div>
      <div className="um-hero" style={{ marginTop: 14 }}>
        <div className="eyebrow">reference</div>
        <h1>{UM_TITLES[activeNode] || 'the manual'}</h1>
      </div>
      <Tabs tabs={UM_TABS} value={tab} onChange={setTab}/>
      <div style={{ marginTop: 20 }}>{panels[tab]}</div>
    </section>
  );

  return (
    <React.Fragment>
      <style>{UM_CSS}</style>
      <div className="um-wrap">
        {view === 'map' ? cover : details}
      </div>
    </React.Fragment>
  );
};

window.UserManualPage = UserManualPage;
