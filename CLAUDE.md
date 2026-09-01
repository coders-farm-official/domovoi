# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Domovoi** — a local-first home voice assistant published by Coders Farm
(github.com/coders-farm-official/domovoi). A domovoi is a Slavic household
guardian spirit, often taking the form of a cat; the cat mascot appears
throughout the UI. The system runs on the user's own hardware: a central
server, Raspberry Pi wake-word satellites around the house, a LAN web
dashboard, and an Android app.

**Host platforms: Linux-first, Windows fully supported.** Linux is the
primary target for a dedicated always-on server; Windows stays first-class
because the household gaming PC is often the best GPU in the house. Neither
is a second-class citizen — when adding platform-touching code, make the
POSIX path work and keep the Windows path intact, guarded by
`sys.platform`. Whisper on CUDA is one supported configuration, not an
assumption: `whisper_device` is `cuda` or `cpu`, and the CUDA runtime
wheels live in their own `cuda` extra. See `docs/LINUX_HOST.md` and
`docs/CPU_HOST.md`.

| Path | What it is |
|---|---|
| `domovoi/` | Core service (FastAPI, port **6370**): STT → intent routing → handlers → TTS, WebSocket streaming for satellites, persistence, background workers, the plugin runtime. |
| `web/` | Management dashboard (separate FastAPI process, port **6369**): reads the same Postgres, proxies live-state actions to core admin endpoints, no-build Babel React frontend. |
| `satellite/` | Raspberry Pi client — wake word, VAD, barge-in, audio streaming. Config in `~/.domovoi/`, systemd unit `domovoi-satellite.service`. |
| `android/` | Kotlin/Compose app (`com.domovoi.app`) — dashboard parity + local player. |
| `plugins/` | Bundled plugins. `plugins/radio/` is the open-source radio-stations plugin from Coders Farm and the reference example for plugin development. |
| `docs/` | User and developer documentation. Start at [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); don't duplicate its content here. |

## Permanent conventions — non-negotiable for all changes

These are permanent repo rules, not transitional ones. Gate scripts and
reviews enforce them.

1. **Branding:** the ONLY product/bot/machine name is Domovoi. The
   following reserved token patterns must never appear literally in this
   repo (any case): `har[l]ey`, `ric[h]ard`, `orche[s]trator` — not in
   code, comments, docs, config, assets, or examples. Where prose needs
   to refer to the host machine, say "the Domovoi server".
2. **No media-provider brand names:** this repo must contain ZERO literal
   references to specific external media/download services or the tools
   that scrape them (patterns `yout[u]be`, `yt[-_]?d[l]p`, any case).
   Core uses generic vocabulary only: "media provider", "acquisition",
   "external source". Provider-specific functionality ships as
   separately-installed plugins that do not live in this repo.
   (The bracketed patterns above match the banned tokens without being
   them — reuse that trick in any in-repo gate script.)
3. **Ports:** web dashboard `6369`, core voice API `6370`. The token
   patterns `87[6]5` and `80[0]0` must never appear as service-default
   ports. Per-room MPD stays at control 6650+N / http-stream 8050+N.
   Postgres publishes host port 6432. Ollama 11434.
4. **Config dirs:** server-side `~/.domovoi/`; satellite `~/.domovoi/` on
   the Pi. Database `domovoi` (test DB `domovoi_test`).

## Common commands

Run `pip install` and `pytest` from the **repo root** (where `pyproject.toml`
lives). Docker commands run from `domovoi/` (where the compose file lives).

```powershell
# One-shot dev bootstrap (Postgres + Flyway + core)
./domovoi/scripts/dev.sh             # bash / git-bash
./domovoi/scripts/dev.ps1            # PowerShell

# Manual equivalent
pip install -e ".[dev,real-clients,voice-profile]"
pip install -e ".[cuda]"                  # only on an NVIDIA host
pip install --no-deps resemblyzer         # Windows quirk — see domovoi/README.md
                                          # (on Linux, plain `pip install resemblyzer` works)
cd domovoi
docker compose up -d postgres
docker compose run --rm flyway            # prod migrations
docker compose run --rm flyway-test       # test-DB migrations
cd ..
python -m domovoi.main               # core voice service on :6370
python -m web.backend.main           # web dashboard on :6369 (separate process)

# Web dashboard (separate process)
./web/scripts/dev.sh                 # bash
./web/scripts/dev.ps1                # PowerShell

# Tests (conftest forces USE_STUBS=true and refuses non-_test DB)
pytest
pytest domovoi/tests/test_router.py

# Plugin dev loop (console script from pyproject: domovoi = plugins_runtime CLI)
domovoi plugin new <slug>            # scaffold manifest + package + tests
domovoi plugin dev <path>            # register a plugin in place (dev mode)
domovoi plugin pack <path>           # validate + build the installable zip

# Web — regenerate OpenAPI spec after endpoint changes
python -m web.scripts.dump_openapi   # writes web/openapi.json
```

## Key invariants — break these and tests break

- **Local-first.** Every handler declares `requires_network: "no" | "degraded" | "yes"`.
  If not `"no"`, it must implement `fallback_offline()` — enforced by
  `domovoi/tests/test_registry.py`. The router consults `ctx.online`.
- **Intent logging is non-optional.** Every routed turn writes one
  `intents_log` row AND one `conversation_log` row, centrally in
  `router._persist_turn`.
- **The DB is migration-only.** Core migrations live in
  `domovoi/db/migrations/` (Flyway, append-only, V001 frozen once cut).
  Plugins own their migrations in their own Postgres schema — plugins never
  run DDL against core tables.
- **Tests must use the test DB.** `domovoi/tests/conftest.py` derives
  `<dbname>_test` from `DATABASE_URL` and refuses to run otherwise.
- **Stubs vs. real clients.** `USE_STUBS=true` (set by conftest) makes
  Whisper/Ollama/TTS deterministic fakes. Real clients install via extras.
- **MPD is lazy-provisioned per room** by `domovoi/mpd_provisioner.py`; the
  `mpd_rooms` table persists port/container assignments. Not in compose.
- **Handler ordering is correctness.** Fast-path priority is explicit
  (priority bands documented in `domovoi/handlers/__init__.py`); greedy
  catch-all patterns must stay in the last band or they silently poach
  other handlers' phrasings.
- **Windows host.** DLL preloading in `domovoi/bootstrap.py` must run before
  anything imports `ctranslate2`/`faster-whisper`. Don't reorder imports in
  `domovoi/main.py`. Keep `NullPool` in `domovoi/db/session.py`.

## Two Ollama models, not one

`OLLAMA_MODEL` (default `llama3.2:3b`) answers conversational Q&A.
`OLLAMA_TOOL_MODEL` (default `qwen2.5:14b`) routes tool-call dispatch.
Don't conflate them; change one at a time so a regression points at the
right knob.

## Where to read more

- Architecture and process boundaries: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- HTTP/WebSocket surface: [docs/API_REFERENCE.md](docs/API_REFERENCE.md)
- Writing a plugin (manifest, SDK, migrations, packaging): [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) — `plugins/radio/` is the worked example
- Contribution workflow and code standards: [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)
- Satellite hardware (Pi Zero 2 W / Pi 4, ReSpeaker 2-Mics HAT / XVF3800 USB): [docs/SATELLITE_HARDWARE.md](docs/SATELLITE_HARDWARE.md) plus `satellite/PROVISIONING.md`
- Security model (setup code, admin password, LAN posture): [docs/SECURITY_PRIVACY.md](docs/SECURITY_PRIVACY.md)
- UI work: load the `.claude/skills/domovoi-design/` skill — tokens, brand rules, and the pointer to the live component kit in `web/static/`.
