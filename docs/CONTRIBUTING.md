# Contributing to Domovoi

Thanks for helping look after the house spirit. This guide covers setting up
a development environment, running the tests, which dependencies (and
licences) we take, the code conventions the repo actually follows,
migration discipline, what we expect from pull requests, how to publish a
plugin, and the repository gates every change must keep green.

Related reading: [Architecture](ARCHITECTURE.md) ·
[Plugin Development](PLUGIN_DEVELOPMENT.md) ·
[API Reference](API_REFERENCE.md) · [Troubleshooting](TROUBLESHOOTING.md)

Domovoi is MIT-licensed ([LICENSE](../LICENSE)); contributions are accepted
under the same license.

A new **Android dependency** needs its licence decided by hand: check it at
the project's source, then add (or confirm) its rule in
`android/app/src/main/java/com/domovoi/app/ui/screens/settings/OpenSourceLicences.kt`.
The unit tests read the build's own list of libraries and fail until every
one has a permissive rule its POM agrees with; the app's licences screen is
made from the same list.

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
  ".[real-clients,cuda]"`. Without one, set `whisper_device=cpu`
  (`whisper_compute_type=auto` then runs int8; [CPU_HOST.md](CPU_HOST.md))

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
pip install --no-deps -r requirements-no-deps.in   # faster-whisper, resemblyzer (see below)
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

**Pinned installs.** Two hash-pinned locks sit at the repo root.
`requirements.lock` is the set the suite is run against (core + `dev`,
Python 3.12):

```bash
pip install --require-hashes -r requirements.lock
pip install --no-deps -e .
```

`requirements-linux-py314.lock` is what a Linux server installs: core,
`real-clients`, `voice-profile`, `resemblyzer` and CPU torch, for CPython
3.14 on x86_64 ([LINUX_HOST.md, Install](LINUX_HOST.md#install); the
update unit's re-sync uses it on a box that opts in with
`DOMOVOI_USE_LOCK=1`). Regenerate it with
`DOMOVOI_LOCK_SEED=requirements.lock bash scripts/linux/compile-linux-lock.sh`,
which runs pip-compile in a pinned `python:3.14-slim` container, so it
resolves for the server's platform from any machine with Docker, starting
from the dev lock's versions so every package the two share stays at the
version the suite runs.

When you change a dependency in `pyproject.toml`, regenerate both: the dev
lock first, from a venv that has the versions you actually tested (recipe
in its header), then the Linux lock seeded from it, and commit them
together. The floors on `starlette`,
`python-multipart`, `requests` and `pillow` are one-way — raise freely,
never lower; `domovoi/tests/test_ops_dependency_floors.py` holds the line.

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
pytest -n 4                              # full suite on 4 workers (see "In parallel")
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

* `pytest` runs from the repo root; `testpaths` covers **all three** test
  trees in the repo — `domovoi/tests`, `plugins/radio/tests` and
  `satellite/tests`. The bundled plugin's suite and the Pi client's suite
  are part of the repo suite. There is no fourth: `web/` has no test
  directory of its own, the dashboard is tested from `domovoi/tests`.
* `domovoi/tests/conftest.py` runs at import time and, before any other
  domovoi import: forces `USE_STUBS=true`, derives the test database URL by
  appending `_test` to the `DATABASE_URL` dbname, and **refuses to run** if
  the resolved dbname doesn't end in `_test`. This is the single line
  protecting your real data — the suite TRUNCATEs tables. Never weaken it.
* Your home is protected the same way. The repo-root `conftest.py`, loaded
  before any other, points `HOME`/`USERPROFILE` at a temp folder of each
  process's own (each `-n` worker too) and `DOMOVOI_HOME` at its
  `.domovoi`, so `Path.home()`, `~` and every default built from them land
  there. It also records your real `~/.domovoi` at the start, and a run that
  created, changed or removed anything in it **fails**, naming the paths
  (`domovoi/tests/home_isolation.py`). A test that needs a home of its own
  uses `tmp_path`. If something else on the machine writes there during a
  run (a satellite running as you), `DOMOVOI_TEST_HOME_GUARD=off` skips the
  check, never the isolation.
* The test DB must exist and be migrated (`docker compose run --rm
  flyway-test` from `domovoi/`). Plugin migrations apply to both DBs
  automatically through the plugin migration runner.
* Async tests need no decorators (`asyncio_mode = "auto"`).
* Stubs make Whisper/Ollama/TTS deterministic; workers marked
  `stub_suppressed` don't start under stubs. Don't write tests that need
  real network, GPU, or audio hardware.
* The one opt-in exception is the routing corpus
  (`scripts/routing_corpus.json`): `domovoi/tests/test_routing_corpus.py`
  asserts everything around the model-driven router step without a DB or a
  model, and additionally POSTs the corpus at a running core when
  `DOMOVOI_EVAL_CORE_URL` is set (skipped otherwise). The same corpus runs
  from the command line via `scripts/eval_routing.py --core URL` or
  `--ollama URL --model NAME`; run it before and after any change to the
  routing prompt, a `tool_schema` description, or an `offers_tool` gate.

### In parallel: `pytest -n`

```powershell
pytest -n 4                              # 4 worker processes (pytest-xdist, in the dev extra)
```

* **One database per worker.** Under `-n`, the conftest gives every
  worker its own copy of the test database the run resolved (the "lane"):
  lane `domovoi_test` → `domovoi_gw0_test`, `domovoi_gw1_test`, …; lane
  `domovoi8_test` → `domovoi8_gw0_test`, …. A copy is made with
  `CREATE DATABASE … TEMPLATE <lane>` the first time a worker needs it and
  reused after that; when the lane's Flyway history changes (you migrated
  it), each copy is remade from it on the next run. So keep migrating the
  lane only, as before. The role needs `CREATEDB` (the compose `domovoi`
  user has it). Details: `domovoi/tests/xdist_db.py`.
* **One run per lane at a time** still holds, serial or parallel: copying
  needs the lane to have no other connections, and two runs on one set of
  copies truncate each other's tables. Different lanes
  (`TEST_DATABASE_URL=…/domovoi8_test`) run side by side.
* **Mind Postgres' connections.** A test that starts the core app opens
  about 18 connections at once while the lifespan brings its workers up,
  so each worker can burst to ~20, and N workers can line their bursts up.
  Against the default `max_connections` of 100, shared with every other
  run on the same server, that makes `-n 4` the safe choice; past it the
  server can run out ("too many clients"), which fails tests in your run
  *and in everyone else's*. The run says so at the start when the numbers
  don't fit (`pytest -n 8: each worker can open ~20 Postgres connections
  …`). More workers need a server with more connections.
* **What doesn't run under `-n`:** tests marked `bash_harness` (in
  `pyproject.toml`): the three whole-harness wrappers
  (`test_apply_update_harness`, `test_provision_harness`,
  `test_install_update_unit_harness`). Each takes 15–70 minutes under
  Git Bash and would set the wall time of the whole run, so they are
  deselected there and the summary says so; run the harnesses with the
  container runner below, beside the pytest run. A serial `pytest` runs them
  as it always has.
* **What runs on one worker, in turn:** tests that share something a
  database per worker doesn't separate carry `@pytest.mark.xdist_group(name)`,
  and every test of a group runs on the same worker. Today: `pg-roles`, the
  modules that create or drop plugin roles (`test_plugin_installer`,
  `test_plugin_loader`, `test_plugin_upgrade_restart`,
  `test_plugin_migrations*`, `test_plugin_migration_role`), because a
  Postgres role belongs to the server, not to one database; and
  `tmp-domovoi-req-core`, the two tests of the satellite image's
  requirements filter, which writes a fixed `/tmp` file. Groups need
  `--dist loadgroup`, so the conftest makes that the default under `-n`
  (an explicit `--dist` wins). A new test that shares a server-wide or
  machine-wide thing (a role, a fixed path, a fixed port) joins a group,
  or better, stops sharing it.
* A test whose parametrize ids differ between two collections (random
  values in the id) stops an `-n` run before it starts ("Different tests
  were collected"): give such parameters fixed `ids=`.
* Remove the copies when you're done with a lane (they are cheap to remake):
  `python -m domovoi.tests.xdist_db --drop` (the lane `TEST_DATABASE_URL`
  names; `--list` to look first).

### The bash harnesses

The Linux scripts (`scripts/linux/*.sh`) are tested by hermetic bash
harnesses in `scripts/linux/tests/`: `test-apply-update.sh`,
`test-provision.sh` and `test-install-update-unit.sh`. Each case builds a
throwaway repository and PATH shims; nothing touches the machine. Run them
in a pinned Linux container (Ubuntu 26.04, as the server; Docker needed):

```bash
scripts/dev/run-linux-harness.sh apply-update                 # one harness, whole
scripts/dev/run-linux-harness.sh all                          # all three, side by side
scripts/dev/run-linux-harness.sh provision --case 'media_*'   # only some cases
scripts/dev/run-linux-harness.sh apply-update --list          # the case names
```

The runner builds the image from `scripts/dev/linux-harness.Dockerfile`
once (cached by a hash of that file), streams a copy of the checkout in —
tracked files as they are on disk plus new untracked ones — and runs the
harness with no network. It writes nothing into the checkout but its logs,
in `.test-results/linux-harness/` (ignored by git). On Windows run it from
Git Bash; in PowerShell, `bash` is WSL's launcher, so call Git Bash by its
path (`& "C:\Program Files\Git\bin\bash.exe" scripts/dev/run-linux-harness.sh all`).

In the container the harnesses take one to two minutes each; under Git Bash
the same cases take from half an hour to two hours on a busy Windows box
(2026-10-12, same commit: apply-update 72 s against 2 h 06 min,
provision 128 s against 2 h 05 min, install-update-unit 19 s against
41 min), because every case forks thousands of small processes and process
creation is what MSYS is slowest at. Under that load Git Bash also fails
cases whose steps have time bounds, and skips the ones that need symlinks;
the container runs them all. Git Bash still runs them directly, with the
same options:

```bash
bash scripts/linux/tests/test-apply-update.sh --case noop_restart --case 'searxng_*'
```

`--case` takes a case name (the function name without its `case_` prefix)
or a bash glob, and repeats; a `--case` that matches nothing is an error, so
a typo can't pass as a green run. While working on a script, run the cases
you added or changed; the whole harness runs once per batch.

### Known failures

`domovoi/tests/known_failures.toml` lists the tests that fail on main for
a recorded reason (node id, reason, the commit it was first seen on). The
end of every run sorts the failures against it:

```
================ known failures: 1 new, 3 known =================
NEW    domovoi/tests/test_x.py::test_y
known  domovoi/tests/test_whisper_local_files.py::test_… (error in setup)  [since eb4a497] huggingface_hub is not installed …
…
4 failed, 11200 passed, 1 new failure, 3 known failures in 612.3s
```

Only the `NEW` ones are yours to explain. The list only labels: a known
failure still fails, prints its traceback and makes the exit status
non-zero. When a fix lands, take its entry off; the summary prints
`passed …` for an entry whose test passed.

New code needs tests in the same PR. The house pattern is tiered: pure
functions get plain unit tests, handler fast-paths get regex + behavior
tests, worker `tick()`s run against recording doubles
(`domovoi/sdk/testing.py`), and DB-touching code uses the shared fixtures
with per-test truncation.

---

## Dependencies and licences

Domovoi is MIT, and an install has to stay something anyone may run, copy
and ship: a household, the Windows installer, Coders Farm. So, for anything
installed with Domovoi (core, dashboard, satellite, bundled plugins):

* **Permissive licences only**: MIT, BSD, ISC, Apache-2.0, PSF, public
  domain. **No GPL.** **Avoid LGPL** wherever a modest amount of our own
  code does the job; when one can't be avoided, raise it with the
  maintainer instead of adding it. MPL-2.0 is acceptable for third-party
  code we use unmodified (certifi and tqdm on the server, Eigen inside the
  phone's native libraries); never patch such files.
* **Check the licence at the source**: the package's own LICENSE / COPYING
  and file headers, and what its wheel bundles, not only the PyPI
  classifier. A BSD wrapper can carry an LGPL library (soundfile ships
  libsndfile, PyAV ships FFmpeg's libraries).
* **Check the wheels**: cp314 manylinux x86_64 (a Linux server's Python),
  or pure Python ([LINUX_HOST.md](LINUX_HOST.md)).
* **An external program is not a dependency.** ffmpeg / ffprobe, mpg123 and
  fpcalc are separate programs the OS package manager installs; Domovoi
  runs them and links nothing. Prefer that to a Python binding of a
  copyleft library.

Replaced so far:

| Package | Licence | Replaced by |
|---|---|---|
| mutagen | GPL-2.0-or-later | `domovoi/media_tags`, Domovoi's own tag reader (2026-10-15) |
| python-mpd2 | LGPL-3.0-or-later | `domovoi/clients/mpd_protocol.py` (2026-10-15) |
| lameenc | LGPL-3.0, bundles LAME | the system `ffmpeg`, run as a program (2026-10-15) |
| librosa → soxr, soundfile | LGPL-2.1 libsoxr, libsndfile inside | numpy mel spectrogram in `domovoi/clients/speaker_encoder.py`; `ffmpeg` decode in the radio fingerprinter (2026-10-15) |
| av (PyAV, via faster-whisper) | FFmpeg's LGPL libraries inside | audio arrays handed to faster-whisper, installed `--no-deps` (2026-10-15) |
| piper-tts, piper-phonemize | GPL-3.0 (espeak-ng inside) | `domovoi/phonemizer` on ONNX Runtime (2026-10) |
| edge-tts | LGPL-3.0 | dropped with the online voices (2026-10-12) |
| Unidecode / num2words | GPL / LGPL | anyascii / `spoken_names.int_to_words` |

`domovoi/tests/test_dependency_licences.py` fails if a replaced package
comes back as an import in shipped code, a requirement or a lock pin.

Installed without their declared dependencies (`requirements-no-deps.in`,
since 2026-10-15): faster-whisper, which declares PyAV (`av`, FFmpeg's
LGPL libraries inside) though Domovoi hands it arrays and never a file,
and resemblyzer, which declares librosa (with soxr and soundfile:
libsoxr and libsndfile, LGPL) though Domovoi computes the one mel
spectrogram it needs with numpy (`domovoi/clients/speaker_encoder.py`).
So none of PyAV, librosa, soxr or soundfile is installed. The wake-word trainer
image (`scripts/wake_word/docker`) is a separate tool built on the server
when training is turned on, not part of an install; its upstream training
code uses mutagen and espeak-ng.

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
  vocabulary, port and local-machine rules are enforced, not aspirational.
* Small, focused diffs. Refactors and behavior changes travel separately.
* **Don't break the invariants** listed in the repo's `AGENTS.md` and
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
5. **No local machine details.** The repo is public: no home-folder path
   with a real user name, no path into a maintainer's own workspace or
   their functional test harness (it lives outside the repo; a test that
   reads its files takes the folder from an environment variable and
   skips without one), and no real home-network addresses, host names or
   Wi-Fi names. Examples use the user `alex` (`C:/Users/alex`,
   `/home/alex`, `%USERPROFILE%`) and the example network `192.168.1.0/24`:
   router `.1`, server `.20`, PC `.30`, satellites `.4x`. The
   `192.168.0.0/16` block itself is fine. Enforced by
   `domovoi/tests/test_repo_privacy.py`.

If you're writing a gate script: scan case-insensitively over the whole
tree, join the bracket-split fragments to build the actual patterns at
runtime, and allowlist nothing.

---

## Questions

Open an issue at
[github.com/coders-farm-official/domovoi](https://github.com/coders-farm-official/domovoi)
— or check the [FAQ](FAQ.md) and [Troubleshooting](TROUBLESHOOTING.md)
first.
