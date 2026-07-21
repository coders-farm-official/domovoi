# Domovoi Web

Local-network management dashboard for the Domovoi voice assistant.
Browse and edit everything the core owns — music library, playlists,
satellites, people, calendar, news, documents, plugins — without
writing SQL. Separate FastAPI process on port **6369**; the core voice
service runs on **6370**.

---

## Quick start

Both processes run from the **repo root** (where `pyproject.toml`
lives). After `pip install -e ".[dev]"` and starting Postgres
(`docker compose -f domovoi/docker-compose.yml up -d postgres`, host
port 6432) with migrations applied:

```powershell
# Terminal 1 — core voice service (port 6370)
python -m domovoi.main

# Terminal 2 — web dashboard (port 6369)
python -m web.backend.main
```

Open `http://localhost:6369/` (or `http://<server>:6369/` from any
device on the LAN). Hash routing, so deep links like `#satellites`
and `#plugins` work.

The web backend runs on its own — read pages still render straight
from Postgres with the core down. Anything needing live core state
(music transport, announcements, plugin installs) surfaces an honest
toast until the core is up.

## Layout

```
web/
├── backend/
│   ├── main.py               # FastAPI app: routers, lifespan, static mount
│   ├── db.py                 # session factory shim (same Postgres as core)
│   ├── domovoi_client.py     # best-effort HTTP client for core /v1/admin/*
│   ├── realtime.py           # poll loop + LISTEN/NOTIFY → /ws/state fanout
│   ├── plugin_host.py        # plugin web-module loading (see below)
│   └── api/                  # one module per resource (music, people, …)
│       ├── plugins.py        # install/enable/disable/uninstall/upgrade proxy
│       ├── capabilities.py   # /api/capabilities (+/manual) for Android/manual
│       └── acquisitions.py   # generic media-acquisition queue readout
├── static/                   # zero-build React frontend (Babel-in-browser)
│   ├── index.html            # shell + plugin bootstrap (loads plugin scripts)
│   ├── data.js               # fetch helpers + shared /ws/state bus + hooks
│   ├── components.jsx        # UI kit + data-driven Sidebar/Topbar
│   ├── plugins.jsx           # Plugins admin page (two-phase install + trust UX)
│   ├── player.jsx            # in-browser player + plugin player-source registry
│   └── <page>.jsx            # one file per core page
├── samples/                  # illustrative API payloads (docs, not fixtures)
├── scripts/dump_openapi.py   # writes web/openapi.json from the live app
└── openapi.json              # generated — python -m web.scripts.dump_openapi
```

## Conventions

* **Two processes, one database.** The web backend reads the same
  Postgres the core writes. Live process state (connected satellites,
  now-playing stamps) is proxied from core `/v1/admin/snapshot` and
  cached by the poll loop.
* **Realtime**: a 1.5 s poll loop is authoritative; Postgres
  LISTEN/NOTIFY accelerates specific channels to ~50 ms. Clients get a
  single WebSocket at `/ws/state` (see `realtime.py`'s docstring for
  the channel list). Plugin channels register from manifests.
* **Auth (design §7)**: everyday reads and playback are open on the
  LAN. Admin actions — plugin management, config, satellite pushes —
  need an admin session: `POST /api/auth/login` returns a Bearer token
  (argon2id password, sha256-stored sessions). Mutations accept ONLY
  `Authorization: Bearer`; the SameSite=Strict cookie exists solely so
  GET page loads render authenticated state. The dashboard pops its
  login modal automatically on any 401/403.
* **Zero-build frontend.** Babel-standalone compiles JSX in the
  browser; all vendor assets are local (`static/vendor/`). Top-level
  scope is shared across `<script type="text/babel">` tags — reach
  hooks via `React.useState` etc. outside `components.jsx`, and expose
  cross-file symbols via `Object.assign(window, {...})`.
* **Vocabulary**: the core surface is provider-agnostic. Media
  fetching is an "acquisition" (`/api/acquisitions`, design §4.8);
  provider-specific search/download UIs belong to plugin pages.

## How plugin pages mount (design §5)

The web process discovers plugins from the `plugins` registry table —
it never imports plugin core code, and a `sys.meta_path` guard
(installed at boot by `plugin_host.py`) refuses any `domovoi.*` import
in this process except `domovoi.webkit`.

For each enabled plugin whose manifest declares a `web` entry point:

1. `install_dir` goes on `sys.path`; `domovoi_plugin_<slug>.web` is
   imported and `register_web(ctx)` is called with a
   `WebPluginContext` (logger, httpx factory, plugin-schema-scoped DB
   sessions, a `CoreClient` that forwards the caller's admin
   credentials to core).
2. Routers mount at `/api/plugins/<slug>/...` behind a per-slug gate
   that 404s while the plugin is disabled. Static assets serve from
   `<install_dir>/web/static` at `/plugins/<slug>/static/...`.
3. Manifest `[[realtime]]` entries extend the NOTIFY→channel map and
   the poll loop's snapshot helpers (module-level `SNAPSHOTS` dict on
   the plugin's web module).

The frontend shell is data-driven off **`GET /api/plugins/manifest`**
(open): the bootstrap in `index.html` fetches each declared script's
text, wraps it in an IIFE, Babel-transforms it, and executes it —
pages register under `window.DomovoiPlugins.<slug>.pages.<Name>`.
Routes, sidebar items (interleaved with core `nav_order` values:
music=10 … settings=100), badges, and browser-player source kinds all
come from the manifest. A `plugins_changed` NOTIFY re-syncs the
backend live and shows connected dashboards a "reload to get new
pages" toast; upgraded plugin *web code* needs a web-process restart
(Python can't re-import a loaded module safely).

Plugin installs go through the two-phase flow proxied to the core:
stage & validate → preview → an **unskippable trust screen** (§7.5)
→ confirm. See the Plugins page (`static/plugins.jsx`).

## OpenAPI

`web/openapi.json` is a generated artifact:

```powershell
python -m web.scripts.dump_openapi
```

The live doc at `/docs` is always current — the plugin host clears the
FastAPI OpenAPI cache whenever routers mount or unmount.
