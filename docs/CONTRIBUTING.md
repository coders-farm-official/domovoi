# Contributing to Domovoi

Thanks for helping look after the house spirit. This guide covers setting up
a development environment, running the tests, the code conventions the repo
actually follows, migration discipline, what we expect from pull requests,
how to publish a plugin, and the repository gates every change must keep
green.

Related reading: [Architecture](ARCHITECTURE.md) ·
[Plugin Development](PLUGIN_DEVELOPMENT.md) ·
[API Reference](API_REFERENCE.md) · [Troubleshooting](TROUBLESHOOTING.md)

Domovoi is MIT-licensed ([LICENSE](../LICENSE)); contributions are accepted
under the same license.

---

## Development setup

Prerequisites:

* **Python 3.11+** (the codebase uses 3.11 syntax throughout)
* **Docker** (Postgres + Flyway run in containers; Postgres publishes host
  port **6432**)
* **Linux, Windows, or macOS** all work for development. Linux and Windows
  are both supported server hosts (see [LINUX_HOST.md](LINUX_HOST.md));
  the CUDA/DLL paths are Windows-specific and stubbed in tests
* Optional for real voice work: [Ollama](https://ollama.com) on `:11434`,
  plus an NVIDIA GPU if you want Whisper on CUDA — `pip install -e
  ".[real-clients,cuda]"`. Without one, set `whisper_device=cpu` and
  `whisper_compute_type=int8` ([CPU_HOST.md](CPU_HOST.md))

One-shot bootstrap (brings up Postgres, runs migrations, starts the core):

```bash
./domovoi/scripts/dev.sh         # bash
```

```powershell
./domovoi/scripts/dev.ps1        # PowerShell
```

Manual equivalent — note the directory dance: `pip install` and `pytest` run
from the **repo root** (where `pyproject.toml` lives); Docker commands run
from `domovoi/` (where the compose file lives):

```powershell
pip install -e ".[dev,real-clients,voice-profile]"
pip install --no-deps resemblyzer         # Windows quirk — see below
cd domovoi
docker compose up -d postgres
docker compose run --rm flyway            # prod-DB migrations
docker compose run --rm flyway-test      # test-DB migrations
cd ..
python -m domovoi.main                    # core voice service on :6370
python -m web.backend.main                # web dashboard on :6369 (separate process)
```

For test-only work, `pip install -e ".[dev]"` is enough — the suite runs
entirely on deterministic stubs (`USE_STUBS=true`), no GPU, no Ollama, no
audio hardware.

**The resemblyzer quirk**: Resemblyzer pins `webrtcvad`, which has no Windows
binary wheels. Our code path never calls the part that needs it, so install
the voice-profile extras first and Resemblyzer with `--no-deps`. Details in
[`domovoi/README.md`](../domovoi/README.md).

Databases: `domovoi` (prod) and `domovoi_test` (tests), both owned by
Flyway for core tables. Config lives under `~/.domovoi/` on the server (and
under `~/.domovoi/` on satellites).

### Useful commands

```powershell
pytest                                   # full suite
pytest domovoi/tests/test_router.py      # one file

domovoi plugin new <slug>                # scaffold a plugin
domovoi plugin dev <path>                # register a plugin dir in place
domovoi plugin pack <path>               # validate + build the installable zip

python -m web.scripts.dump_openapi       # regenerate web/openapi.json after endpoint changes
```

---

## Running the tests

```powershell
pytest
```

Facts about the suite you should know before touching it:

* `pytest` runs from the repo root; `testpaths` covers **both**
  `domovoi/tests` and `plugins/radio/tests` (the bundled plugin's suite is
  part of the repo suite).
* `domovoi/tests/conftest.py` runs at import time and, before any other
  domovoi import: forces `USE_STUBS=true`, derives the test database URL by
  appending `_test` to the `DATABASE_URL` dbname, and **refuses to run** if
  the resolved dbname doesn't end in `_test`. This is the single line
  protecting your real data — the suite TRUNCATEs tables. Never weaken it.
* The test DB must exist and be migrated (`docker compose run --rm
  flyway-test` from `domovoi/`). Plugin migrations apply to both DBs
  automatically through the plugin migration runner.
* Async tests need no decorators (`asyncio_mode = "auto"`).
* Stubs make Whisper/Ollama/TTS deterministic; workers marked
  `stub_suppressed` don't start under stubs. Don't write tests that need
  real network, GPU, or audio hardware.

New code needs tests in the same PR. The house pattern is tiered: pure
functions get plain unit tests, handler fast-paths get regex + behavior
tests, worker `tick()`s run against recording doubles
(`domovoi/sdk/testing.py`), and DB-touching code uses the shared fixtures
with per-test truncation.

---

## Code style

There is no formatter config in the repo; match the code around you. What
the codebase actually does, consistently:

* **Modern typed Python.** `from __future__ import annotations` at the top
  of every module; full type hints on public signatures; `X | None` over
  `Optional[X]`; `dataclasses` (often `frozen=True`) for value types;
  `Protocol` for structural seams; `Literal` for closed string sets.
* **Docstrings explain *why*, not just what.** Module docstrings state the
  module's contract and its load-bearing invariants. Non-obvious decisions
  get a comment naming the reason ("band rationale", "Windows consoles
  default to cp1252", ...). If a change depends on ordering or an invariant,
  say so *at the site*.
* Double quotes, 4-space indent, lines ≈ 79–88 columns, `_leading_underscore`
  for module-private names, `UPPER_SNAKE` module constants, sparse section
  dividers (`# ─── ... ───`) in long modules.
* **No silent `except`.** Broad catches exist only where isolation is the
  point (worker ticks, teardown, best-effort writes) and are annotated
  `# noqa: BLE001 — <reason>` with a log line.
* **SQL** goes through `sqlalchemy.text()` with bound parameters — never
  f-string interpolation of values (schema/column names composed from
  validated constants are the only exception).
* **Async everywhere** in core and web; blocking work goes to subprocesses
  or thread executors.
* **Windows first**: ASCII-only console output (a stray arrow glyph crashes
  cp1252 consoles), path handling through `pathlib`, no reliance on
  inotify, and never reorder the DLL bootstrap imports in
  `domovoi/main.py` / `domovoi/bootstrap.py`. Keep `NullPool` in
  `domovoi/db/session.py`.
* **Handler names are stable identifiers** (they land in `intents_log`);
  priority bands are documented in `domovoi/handlers/__init__.py` — new
  handlers add a `# band rationale:` comment next to `priority_band`.
* Frontend: the dashboard is no-build Babel React; plugin pages register
  only through `window.DomovoiPlugins.<slug>` — never bare globals. For UI
  styling, use the design tokens and component kit under `web/static/`.

---

## Migration discipline

The database is migration-only — no runtime DDL, no ORM auto-create.

**Core** (`domovoi/db/migrations/`, Flyway):

* Append-only. `V001__baseline.sql` is frozen; a cut migration is never
  edited — checksum drift breaks every deployment. Fix mistakes with a new
  migration.
* No down-migrations. Write forward-compatible changes.
* Both DBs: whatever you apply to `domovoi`, apply to `domovoi_test`
  (`flyway` + `flyway-test` compose services).

**Plugins** (`<plugin>/migrations/`, the plugin migration runner):

* Files named `V###__name.sql`, gapless from `V001`, append-only,
  sha256-checksummed into `plugin_<slug>.schema_history` — an applied file
  that changed on disk refuses to load.
* Plugins own **only** their own `plugin_<slug>` schema. The install-time
  SQL lint rejects `CREATE SCHEMA`, `CREATE EXTENSION`, DDL against
  `public.`, references to foreign `plugin_*` schemas, and cross-schema
  `REFERENCES` (use soft refs + events + a reconciliation sweep — see
  [Plugin Development §6.3](PLUGIN_DEVELOPMENT.md#63-per-schema-db-only)).
* The runner applies to prod first, then the `_test` sibling; fresh installs
  are both-or-neither.

Rule of thumb for reviews: if a PR edits an existing migration file, it's
wrong unless that migration has never shipped anywhere.

---

## Pull request expectations

* **Keep the suite green.** All tests pass locally before you push; new
  behavior comes with tests that fail without your change.
* **Keep the gates green** (see below) — branding, media-provider
  vocabulary, and port rules are enforced, not aspirational.
* Small, focused diffs. Refactors and behavior changes travel separately.
* **Don't break the invariants** listed in the repo's `CLAUDE.md` and
  [Architecture](ARCHITECTURE.md): local-first (`requires_network` +
  `fallback_offline`), non-optional intent logging, migration-only DB,
  test-DB-only tests, per-band handler ordering, the Windows import order,
  the two Ollama models (`OLLAMA_MODEL` for Q&A, `OLLAMA_TOOL_MODEL` for
  tool dispatch — change one at a time).
* New dependencies need a stated reason in `pyproject.toml` (look at the
  existing comments — every dep says why it's there) and must have Windows
  wheels.
* Update the docs you invalidate (`docs/`, plugin READMEs, endpoint changes
  → regenerate `web/openapi.json` via `python -m web.scripts.dump_openapi`).
* Public API changes (SDK surface, event payloads, manifest schema, band
  ranges, `core.*` catalog) are versioned — extending the surface is a minor
  bump of `domovoi.sdk.API_VERSION`; removing or changing anything is
  breaking. Call this out explicitly in the PR description.
* PR description: what changed, why, how it was tested, and any invariant it
  touches.

---

## Publishing a plugin

Plugins developed against this repo can live anywhere (only bundled plugins
live in `plugins/`). The full authoring guide is
[PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md); this is the release
checklist.

**Zip layout.** `domovoi plugin pack <path>` produces the canonical shape:
`domovoi-plugin.toml` at the zip root, one `domovoi_plugin_<slug>/` package,
`migrations/`, optional `web/static/`, `requirements.lock`. A zip whose
content sits inside a single top-level directory also installs — that's the
GitHub archive shape, which is what makes install-by-URL work. Caps enforced
at install: 100 MB compressed, 500 MB extracted, 10,000 entries, no
symlinks, no absolute/`..`/backslash paths, no Windows reserved device
names, no case-colliding entries.

**GitHub release + install-by-URL.** Recommended conventions:

* One repo per plugin, `domovoi-plugin-<slug>`, with the plugin tree at the
  repo root (so the archive shape installs directly).
* Tag releases `v<X.Y.Z>` matching the manifest `version` exactly.
* Users install by pasting `https://github.com/<owner>/<repo>[@ref]` into
  the dashboard — no `@ref` means the default branch; pin `@v1.2.0` in your
  install instructions so users get releases, not `main`.
* Ship the packed zip as a release asset too, for offline installs.

**Versioning.** Strict semver `X.Y.Z`. Upgrades are version-monotonic
(same-version reinstall refused; downgrades need `force` and are refused
outright across an applied migration). Declare an honest `domovoi_api`
range (e.g. `">=1.0,<2.0"`) — it's evaluated against the core SDK version at
parse time and an incompatible plugin is refused before anything runs. When
a new Domovoi minor adds SDK surface you depend on, raise your lower bound.

**Requirements.** Exact pins plus a hashed lockfile
(`pip-compile --generate-hashes --output-file=requirements.lock
requirements.in`). Wheels only — a dependency that resolves as an sdist
fails the install dry-run by design.

**Trust and permissions honesty.** The `[permissions]` booleans and
`warnings` in your manifest are shown verbatim on the install trust screen,
which also tells the user plainly that plugins run with full server access.
Name every outbound service, subprocess, and piece of hardware you touch —
the bundled radio plugin's manifest is the bar. Understating behavior is
grounds for delisting from anything Coders Farm recommends.

**Data policy.** Design your schema so uninstall-with-keep is meaningful:
user-created data survives a keep-uninstall and a later reinstall runs
migration catch-up against it.

---

## Repository gates — keep these green

These are permanent repo rules enforced by gate checks and review. The
banned tokens below are written **bracket-split** so this file passes its
own gate; the real patterns are the bracketed fragments joined together
(case-insensitive). Reuse the same trick in any in-repo gate script or doc
that must name them.

1. **Branding.** The only product/bot/machine name is *Domovoi*. The
   following token patterns are permanently banned from the repo — code,
   comments, docs, config, assets, examples — any case: `har[l]ey`,
   `ric[h]ard`, `orche[s]trator`. Where prose needs the host machine,
   write "the Domovoi server".
2. **No media-provider brand names.** Zero literal references to specific
   external media/download services or their scraper tools. Patterns:
   `yout[u]be`, `yt[-_]?d[l]p` (any case). Use the generic vocabulary only —
   "media provider", "acquisition", "external source" — and use the bundled
   radio plugin for examples. Provider-specific functionality ships as
   separately installed plugins that do not live in this repo.
3. **Ports.** Web dashboard `6369`; core voice API `6370`; Postgres
   publishes host `6432`; per-room MPD at control `6650+N` / HTTP stream
   `8050+N`; Ollama `11434`. The patterns `87[6]5` and `80[0]0` must
   never appear as service-default ports.
4. **Config dirs.** Server: `~/.domovoi/`. Satellite: `~/.domovoi/` on the
   Pi. Databases `domovoi` / `domovoi_test`.

If you're writing a gate script: scan case-insensitively over the whole
tree, join the bracket-split fragments to build the actual patterns at
runtime, and allowlist nothing.

---

## Questions

Open an issue at
[github.com/coders-farm-official/domovoi](https://github.com/coders-farm-official/domovoi)
— or check the [FAQ](FAQ.md) and [Troubleshooting](TROUBLESHOOTING.md)
first.
