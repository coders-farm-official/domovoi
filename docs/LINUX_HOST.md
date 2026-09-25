# Running the Domovoi server on Linux

Linux and Windows are both supported server hosts. Windows earned its
first-class status honestly — for many households the gaming PC is the best
AI hardware in the house — but that case rests almost entirely on having an
NVIDIA GPU in it.

On a dedicated always-on box, especially one without NVIDIA, **Linux is the
better host** and it's where the project is heading. This page covers what
actually differs. Nothing here is a workaround for a Windows assumption;
the platform-specific paths are guarded and both are maintained.

Read alongside: [Setup runbook](SETUP_RUNBOOK.md) (the ordered bring-up) ·
[Running without an NVIDIA GPU](CPU_HOST.md) (model and device settings)

---

## What gets better

The Windows-first case rests almost entirely on CUDA. Take CUDA away and
the ledger flips:

| | On Windows | On Linux |
|---|---|---|
| **CUDA DLL preloading** | `domovoi/bootstrap.py` preloads cuBLAS/cuDNN by hand because the Windows loader ignores `add_dll_directory()` for dependent DLLs. The plugin loader asserts it ran. | The whole module is `if sys.platform != "win32": return ()`. Dead code. It's the single biggest piece of Windows-specific engineering in the repo, and you skip it. |
| **Wake-word training** | openWakeWord's training pipeline (piper-sample-generator) is Linux-only, so `wake_word_train_command` has to shell out to WSL2, Docker, or Colab. | Native. Point the setting at the pipeline directly — no bridge. See [`scripts/wake_word/README.md`](../scripts/wake_word/README.md). |
| **External binaries** (`ffmpeg`, `fpcalc`/Chromaprint, `mpg123`) | Download, unzip, put on PATH by hand. | `apt install`. |
| **`resemblyzer` install** | Needs `pip install --no-deps resemblyzer` because its `webrtcvad` pin has no Windows wheels and would need MSVC to build. | `webrtcvad` builds from source with `gcc` + `python3-dev`, so try the plain install first. |
| **Docker** | Docker Desktop — a whole application, its own RAM overhead, its own licensing question. | Native Docker Engine. The core drives containers through the `docker` CLI (`mpd_provisioner.py`), so nothing else changes. |
| **Autostart** | Scheduled task at boot, or a service wrapper. Fiddly. | systemd. Units below. |
| **Ollama on an AMD/Intel iGPU** | ROCm support on Windows is thin. | Better supported, if you ever want to experiment. Still optional — see [CPU_HOST.md](CPU_HOST.md#what-about-the-integrated-gpu). |

## What to know going in

Four differences. Two need something from you; two are already handled
in-repo and are listed so you know why you don't have to think about
them.

### 1. The system-voice fallback needs a package installed

TTS routes `edge → piper → system`. The `system` rung is the OS's own
synthesizer — pyttsx3/SAPI on Windows, and **`espeak-ng` on Linux**. It's
robotic, and that's the point: it's the floor that still talks when the
network is down *and* the Piper voice is missing or broken.

`espeak-ng` isn't installed by default on a server image, and without it
the chain ends at Piper with nothing beneath it. One package:

```bash
sudo apt install -y espeak-ng
```

(It's already in the [prerequisites](#install) below. `espeak` works too if
that's what your distro carries.) Either way, download a Piper voice and
confirm it renders — `espeak-ng` is a safety net, not a voice you want the
house to hear day to day.

### 2. USB satellite adoption needs auto-mounting

The dashboard's adopt-a-satellite-over-USB flow scans for removable
drives. There *is* a real Linux implementation —
`_removable_linux()` in `web/backend/api/files_security.py` parses
`/proc/mounts` and checks `/sys/block/<dev>/removable` — but it only sees
volumes that are **already mounted**. Windows assigns a drive letter
automatically; a headless Ubuntu Server does not mount USB storage at all.

Two smaller degradations in the same flow: the FAT volume-label
pre-filter is skipped (`_volume_label()` returns `None`, and the check is
written so that falls through safely rather than rejecting the drive), and
the Windows raw-volume flush after writing `provision.json` no-ops — but
the write does `fsync` + atomic `os.replace` + a directory `fsync`, so the
rename is durable on POSIX even if the card is yanked immediately.

If you want to try this flow, install `udisks2` and an auto-mount helper,
or point the scanner at a fixed mountpoint:

```bash
export SATELLITE_ADOPTION_SCAN_DIRS=/media/domovoi/setup
```

**My advice: don't start here.** This flow is [already unvalidated on real
hardware](HARDWARE_VALIDATION.md) on its best-supported platform. On Linux
you'd be debugging two unknowns at once. Use the manual path in
[`satellite/PROVISIONING.md`](../satellite/PROVISIONING.md) — it's proven,
and it's the same amount of work the second time you do it.

### 2b. Restart-from-the-dashboard needs one sudoers line

The version panel's **Restart to apply changes** button bounces both
services so pulled code actually loads. The service user can't do that
unaided, so grant exactly that one command — the same single-command
pattern the satellite uses for its own self-restart
([`PROVISIONING.md`](../satellite/PROVISIONING.md) §8.1).

> **Prefer the update unit.** A bare bounce loads new code but does not
> install new dependencies, run new migrations or rebuild the MPD image.
> With `domovoi-update.service` installed ([Updates from the
> dashboard](#updates-from-the-dashboard)) the same button does all of
> that, health-checks the result and rolls back if it fails. It needs its
> own one-line grant, shown there. What follows is the plain bounce, which
> is what the button does on a host without the unit.

```bash
sudo visudo -f /etc/sudoers.d/domovoi-restart
```

```
domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-core.service domovoi-web.service
```

Replace `domovoi` with the `User=` from your unit files. Verify without
actually restarting anything:

```bash
sudo -n -l /usr/bin/systemctl --no-block restart domovoi-core.service domovoi-web.service
```

That's the same probe the server runs to decide whether to offer the
button; `domovoi/self_restart.py` reports `restart_capable` from it. Skip
this and nothing breaks — the panel shows the manual `systemctl` command
instead of a button, which is exactly the pre-existing behaviour.

> Both units are bounced together on purpose: a pull moves the whole
> checkout, and core and web import from the same tree, so restarting one
> would leave the other serving stale code — the confusion the version
> panel exists to end.

### 3. `host.docker.internal` doesn't exist (handled)

Chat mode's Letta container reaches the host's Ollama at
`host.docker.internal`, which Docker Desktop provides and native Docker
Engine does not. Affects `OLLAMA_BASE_URL` in the `letta` service and the
`letta_tool_callback_url` setting.

**This is now handled in-repo** — the `letta` service in
`domovoi/docker-compose.yml` maps the name explicitly:

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Docker Desktop provides the name natively and ignores the mapping, so the
one compose file works on both platforms. Nothing to do; noted here because
any *new* container that needs to reach the host wants the same two lines.

### 4. Cosmetic: no GPU readout (nothing to do)

The dashboard's Models page reads GPU stats from `nvidia-smi`. It's
guarded by `shutil.which()`, so it degrades cleanly to blank rather than
erroring. CPU, RAM, and disk still come through via `psutil`.

---

## Which distro

**Ubuntu Server 26.04 LTS** (verified against this stack, August 2026).
Any current LTS works; the section below is really about *how to check*,
because the answer changes with time.

### The thing that actually decides it

Ubuntu's system Python moves with the release — 24.04 ships 3.12, 26.04
ships **3.14** (`python3 3.14.3-0ubuntu2`). That matters because this stack
leans on compiled packages that ship wheels *per Python version*, and a
brand-new CPython can go months without them. When a wheel is missing, pip
falls back to building from source and you lose an evening to a compiler
error that has nothing to do with Domovoi.

So the rule is: **before committing to a release, check that this repo's
compiled dependencies publish wheels for the Python it ships.** The
long poles are `numba` (pulled in by `librosa`), `torch`, and
`ctranslate2` (under `faster-whisper`) — `numba` is reliably the last to
land, so it's the one to watch.

```bash
python3 -c "import json,urllib.request as u,re; [print(p, sorted({m.group(1) for f in json.load(u.urlopen(f'https://pypi.org/pypi/{p}/json'))['urls'] for m in [re.search(r'-(cp3\d+)-',f['filename'])] if m and 'manylinux' in f['filename'] and 'x86_64' in f['filename']})) for p in ('numba','torch','ctranslate2','shazamio-core')]"
```

If your target Python's tag is in all three lists, you're clear.

### Where that stood in August 2026

Every compiled dependency had cp314 manylinux x86_64 wheels: `torch`
2.13.0, `ctranslate2` 4.8.2, `numba` 0.67.0, `numpy` 2.5.2, `scipy`
1.18.1, `asyncpg` 0.31.0, plus `pillow`, `psutil`, `sqlalchemy`,
`miniaudio`, `lameenc`, `soxr`, `llvmlite`. `piper-tts` ships `cp39-abi3`,
which is forward-compatible with every CPython from 3.9 on. The NVIDIA
CUDA wheels are `py3-none-manylinux` — no Python tag at all.

Two dependencies still compile from source, and both are worth knowing
about because **neither shows up if you only check the packages named in
`pyproject.toml`** — they are transitive:

- **`webrtcvad`** (via `resemblyzer`) — sdist-only on *every* Python
  version, so not a 3.14 issue. See the `resemblyzer` note under
  [Install](#install).
- **`shazamio-core`** (via `shazamio`, the library-enricher fallback) — a
  Rust/pyo3 extension binding ALSA. `shazamio` itself is pure Python, which
  makes this easy to miss. When its prebuilt wheel is absent for your
  Python, pip compiles it, and that needs `pkg-config` + `libasound2-dev`
  from the prerequisites above plus a Rust toolchain (pip fetches one
  automatically).

The lesson generalizes: when checking wheel coverage for a new Python,
walk the *transitive* compiled dependencies, not just the direct ones. A
pure-Python wrapper can hide a Rust extension.

Docker publishes native `resolute` packages, and Ollama's installer
branches on kernel and architecture rather than Ubuntu release.

### If the wheels aren't there yet

Don't fight the system Python — sidestep it:

```bash
uv venv --python 3.13 .venv
```

`uv` (or deadsnakes) installs its own interpreter, so the distro's version
stops mattering and the systemd units below work unchanged — they already
point at the venv's `python`. This is also the escape hatch if a future
release jumps to a Python the ecosystem hasn't caught up with.

Picking an older LTS isn't permanent either: LTS-to-LTS
`do-release-upgrade` opens once the newer release reaches its `.1` point
release.

Use **Server, not Desktop.** A desktop environment costs you 1–2 GB of RAM
that models want, and the server has no screen. (The one thing Desktop
would buy you is USB auto-mounting for the adoption flow — see above, and
you're not using that.)

---

## Install

Prerequisites:

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-dev build-essential pkg-config libasound2-dev git ffmpeg mpg123 libchromaprint-tools espeak-ng
```

`libchromaprint-tools` provides `fpcalc` for library enrichment,
`espeak-ng` is the last-resort TTS voice, and `python3-dev` +
`build-essential` are what let `webrtcvad` compile.

`pkg-config` and `libasound2-dev` are there for **`shazamio-core`** — a
Rust/pyo3 extension that binds ALSA. It's a transitive dependency of
`shazamio` (the library-enricher fallback), and on any Python new enough
to be missing its prebuilt wheel, pip compiles it from source. Without
these two the build dies with *"The pkg-config command could not be
found"* and takes the whole install down with it.

Docker Engine — follow Docker's official apt-repository instructions for
your release, then:

```bash
sudo usermod -aG docker $USER
```

**This one matters.** The core shells out to the `docker` CLI to build and
run each room's MPD container. Without group membership it would need
sudo, which it won't do. Log out and back in, then confirm with `docker ps`
that you get output rather than a permission error.

Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Then clone and install. A virtualenv is worth it here — it keeps Domovoi's
dependency tree away from the system Python that apt manages:

```bash
git clone https://github.com/coders-farm-official/domovoi && cd domovoi
```

```bash
python3 -m venv .venv && source .venv/bin/activate
```

**Install CPU-only torch first.** This step is not optional on a machine
without an NVIDIA GPU:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

PyPI's default Linux `torch` build bundles its own CUDA runtime — cuBLAS,
cuDNN, NCCL, cuFFT, cuSOLVER, Triton and friends, about **2.7 GB** of it —
and `torch` arrives via the `voice-profile` extra. That is entirely
separate from the `cuda` extra below, which only covers the wheels
`ctranslate2` wants. Installing the `+cpu` build first means the next
command sees `torch>=2.0` already satisfied and never pulls the CUDA one.

```bash
pip install -e ".[dev,real-clients,voice-profile]"
```

Then try `resemblyzer` the normal way — the `--no-deps` dance in the
README is a Windows workaround:

```bash
pip install resemblyzer
```

```bash
python -c "from resemblyzer import VoiceEncoder; VoiceEncoder()"
```

That should print `Loaded the voice encoder model on cpu in <N> seconds`.
If the install fails building `webrtcvad`, fall back to `pip install
--no-deps resemblyzer` — Domovoi doesn't use the code path that calls it.

> **CUDA wheels are opt-in.** They used to ride along with `real-clients`;
> they now live in a separate `cuda` extra, so the command above pulls
> nothing NVIDIA. On a machine that *does* have an NVIDIA GPU, add
> `pip install -e ".[cuda]"`.

Bring it up — `dev.sh` is the bash twin of `dev.ps1`:

```bash
./domovoi/scripts/dev.sh
```

The first thing it does on a fresh checkout is write `domovoi/.env` from
`.env.example` with a **random Postgres password** (`python -m
domovoi.env_bootstrap`; both `DATABASE_URL` and the `POSTGRES_PASSWORD`
line that `docker-compose.yml` reads carry it). Run the stack by hand
instead? Run that command first, before the first `docker compose up`, so
the database is initialised with the password your `.env` holds. An
existing `.env` is never touched.

and in a second terminal, from the repo root:

```bash
python -m web.backend.main
```

From here, rejoin the [setup runbook at Step 3](SETUP_RUNBOOK.md#step-3--claim-admin-and-configure-for-your-hardware)
to claim admin and apply your [CPU host settings](CPU_HOST.md).

---

## Make it an appliance

This is where Linux earns its keep. The runbook warns that `dev.ps1` is a
foreground development script that dies with its terminal — on Linux you
replace it properly with three systemd units.

Assumes the repo at `/opt/domovoi`, a venv at `/opt/domovoi/.venv`, and a
service user named `domovoi` who is in the `docker` group. Adjust to taste.

**`/etc/systemd/system/domovoi-db.service`** — brings up Postgres and runs
migrations before anything connects. Exactly what `dev.sh` does first:

```ini
[Unit]
Description=Domovoi database (Postgres + migrations)
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=domovoi
WorkingDirectory=/opt/domovoi/domovoi
ExecStart=/usr/bin/docker compose up -d postgres
ExecStart=/usr/bin/docker compose run --rm flyway

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/domovoi-core.service`** — the voice service on
:6370:

```ini
[Unit]
Description=Domovoi core voice service
After=domovoi-db.service ollama.service
Requires=domovoi-db.service
Wants=ollama.service

[Service]
Type=simple
User=domovoi
WorkingDirectory=/opt/domovoi
Environment=PYTHONUNBUFFERED=1
Environment=HOME=/home/domovoi
ExecStart=/opt/domovoi/.venv/bin/python -m domovoi.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/domovoi-web.service`** — the dashboard on :6369:

```ini
[Unit]
Description=Domovoi web dashboard
After=domovoi-core.service
Wants=domovoi-core.service

[Service]
Type=simple
User=domovoi
WorkingDirectory=/opt/domovoi
Environment=PYTHONUNBUFFERED=1
Environment=HOME=/home/domovoi
ExecStart=/opt/domovoi/.venv/bin/python -m web.backend.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Note `WorkingDirectory` differs: the two Python services run from the
**repo root** so `python -m` resolves the packages, while the database
unit runs from `domovoi/` where the compose file lives. Same split as the
dev scripts. No venv activation is needed — `ExecStart` names the venv's
interpreter directly, which is equivalent.

Two details worth not skipping:

- **`After=ollama.service`** keeps the core from starting before Ollama is
  listening. The core survives it either way, but the first routed turn
  after a reboot shouldn't have to fail first.
- **`Environment=HOME=...`** — `~/.domovoi/` holds your Piper voices,
  trained wake-word models, the sounds cache, and the first-boot setup
  code, all resolved through `Path.home()`. It would very likely resolve
  correctly without this, but a service quietly writing its state into the
  wrong home directory is a miserable thing to diagnose later. Set it and
  point it at the service user's real home.

Enable and start:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now domovoi-db domovoi-core domovoi-web
```

Watch them:

```bash
journalctl -u domovoi-core -f
```

`domovoi-web` uses `Wants=` rather than `Requires=` on the core
deliberately — if the core is wedged, you still want the dashboard up to
tell you so.

**Then test the power cut.** Pull the plug, bring it back, and confirm the
whole house returns — Docker, Postgres, both services, every satellite —
without you logging in. That's the difference between a demo and an
appliance, and it's the thing Linux makes genuinely easy.

---

## Updates from the dashboard

The version panel pulls with `git pull --ff-only`. A pull can bring new
Python dependencies, a new Flyway migration or a new MPD image, and a bare
restart of core and web applies none of them: `domovoi-db` runs Flyway only
when *it* starts. A migration that never ran looks like a broken release
(the V013 device-token table was the first time this bit).

A fourth unit closes that gap. `domovoi-update.service` is a root oneshot
that runs [`scripts/linux/apply-update.sh`](../scripts/linux/apply-update.sh).
Once it's installed, the panel's **Restart to apply changes** starts it
instead of bouncing core and web, and each run does this:

1. Work out what changed since the last SHA it applied and saw healthy.
   If nothing did, it's a plain restart: stop web and core, restart
   `domovoi-db` (a cheap no-op Flyway run), start core and web, and
   health-check them. The button stays fast.
2. Refuse, touching nothing, if tracked files have uncommitted changes. A
   rollback could not restore that tree. Untracked files are fine.
3. `pg_dump -Fc` the database through the `domovoi-postgres` container into
   `/var/lib/domovoi-update/backups/` (the newest 5 are kept). If the backup
   fails, the update stops there and nothing has been stopped.
4. Stop `domovoi-web` and `domovoi-core`.
5. If `pyproject.toml`, a `requirements*.lock` or a bundled plugin's lock
   changed: re-sync the venv the way [Install](#install) builds it (CPU
   torch first, then `pip install -e ".[dev,real-clients,voice-profile]"`).
6. If `domovoi/Dockerfile.mpd` or `domovoi/mpd.conf` changed: rebuild
   `domovoi-mpd:latest` exactly as the core does, and remove the room
   containers. The core recreates each one at startup from its `mpd_rooms`
   row, with the same data volume, so playlists and queues survive.
7. `systemctl restart domovoi-db`: compose up plus Flyway.
8. Start core and web. Both must answer `/v1/health` (core, :6370) and
   `/api/health` (web, :6369) within 120 s.

If any of 4-8 fails, it rolls back: `git reset --keep` to the previous SHA
(never `--hard`), the venv re-synced and every package put back at its
exact pre-update version, the old MPD image rebuilt, and, if
`flyway_schema_history` grew, the pre-update dump restored. The restore goes
into a fresh database that is then renamed to `domovoi`; the replaced one
is kept as `domovoi_failed_<timestamp>` for inspection, and you drop it by
hand when you're done with it. Then it restarts and health-checks again, and
records the commit it rolled back as `bad_sha`. The panel stops offering a
pull while upstream still points at that commit, and offers the next one.

Every run writes `/var/lib/domovoi-update/last-result.json` (status,
from/to SHA, each step with its timing, the error). The version panel shows
it as **last update**, and `GET /v1/admin/version` serves it as
`last_update`. Everything the script prints goes to the journal:

```bash
journalctl -u domovoi-update -n 200
```

**`/etc/systemd/system/domovoi-update.service`**:

```ini
[Unit]
Description=Domovoi update (back up, sync, migrate, restart, roll back on failure)
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-/etc/default/domovoi-update
ExecStart=/bin/bash /opt/domovoi/scripts/linux/apply-update.sh
TimeoutStartSec=30min
```

No `User=`: it runs as root, because it has to stop and start the other
units and read the database dump. It has no `[Install]` section either:
nothing starts it at boot, only the button or you.

Root runs the script straight from the checkout, which the service user can
write. That doesn't give the service user anything new: it is already in
the `docker` group, which is root-equivalent. The script also keeps root
away from the tree: git and pip run as the service user through
`runuser`, and root's own files live in `/var/lib/domovoi-update`, which the
service user can read but not write.

**`/etc/default/domovoi-update`** is optional. Every setting defaults to the
layout on this page, so you only need the file to change one:

```bash
# Service user. Default: User= of domovoi-core.service, else the owner of the checkout.
# DOMOVOI_USER=domovoi
# Default: the checkout the script lives in.
# DOMOVOI_REPO_DIR=/opt/domovoi
# DOMOVOI_VENV=/opt/domovoi/.venv
# Extras for the venv re-sync. An NVIDIA host adds cuda: dev,real-clients,voice-profile,cuda
# DOMOVOI_PIP_EXTRAS=dev,real-clients,voice-profile
# CPU torch index, used first when the extras include voice-profile. Empty: skip that step.
# DOMOVOI_TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
# DOMOVOI_UPDATE_DIR=/var/lib/domovoi-update
# DOMOVOI_UPDATE_KEEP_BACKUPS=5
# 0 lets an update go ahead when the pre-update backup fails (then no restore is possible).
# DOMOVOI_UPDATE_REQUIRE_BACKUP=1
# DOMOVOI_UPDATE_HEALTH_TIMEOUT=120
# DOMOVOI_CORE_HEALTH_URL=http://127.0.0.1:6370/v1/health
# DOMOVOI_WEB_HEALTH_URL=http://127.0.0.1:6369/api/health
# DOMOVOI_PG_CONTAINER=domovoi-postgres
# DOMOVOI_PG_USER=domovoi
# DOMOVOI_PG_DB=domovoi
# Where the core records the pre-pull SHA (its UPDATE_STATE_DIR). Default: ~<service user>/.domovoi/update
# DOMOVOI_CORE_STATE_DIR=/home/domovoi/.domovoi/update
# Default: MPD_IMAGE_TAG / MPD_CONTAINER_PREFIX from domovoi/.env, else these.
# DOMOVOI_MPD_IMAGE_TAG=domovoi-mpd:latest
# DOMOVOI_MPD_CONTAINER_PREFIX=domovoi-mpd-
```

**The sudoers grant.** The button needs one more single-command rule,
alongside (not instead of) the restart rule in [2b](#2b-restart-from-the-dashboard-needs-one-sudoers-line):

```bash
sudo visudo -f /etc/sudoers.d/domovoi-update
```

```
domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block start domovoi-update.service
```

Check it as the service user, without starting anything:

```bash
sudo -u domovoi sudo -n -l /usr/bin/systemctl --no-block start domovoi-update.service
```

How the core decides which restart to run: it looks for
`domovoi-update.service` in the systemd unit directories (a plain file
check, no sudo). Found, the button starts the unit, and only if the rule
above exists. Without that rule the panel says so and shows the manual
command; it does not fall back to a plain bounce, which would skip the
migrations. Not found, the button bounces core and web exactly as before.
`systemctl mask domovoi-update.service` switches back to the plain bounce.

### One-time upgrade for existing installs

For a box already running the three units above, such as the household's
first server. Run these over SSH, as your admin user, in this order.

1. Record the SHA the box is **running** now as the last known-good one.
   Do this first, before you pull. The `/v1/admin/version` endpoint is
   open, so no login is needed:

   ```bash
   sudo install -d -m 0755 /var/lib/domovoi-update
   RUNNING=$(curl -s http://127.0.0.1:6370/v1/admin/version | python3 -c 'import json,sys; print(json.load(sys.stdin)["running_sha"].removesuffix("-dirty"))')
   sudo -u domovoi git -C /opt/domovoi rev-parse --verify "$RUNNING^{commit}" | sudo tee /var/lib/domovoi-update/applied_sha
   ```

   `tee` must print a 40-character SHA. If it prints an error, stop there.

2. Pull, but don't press Restart yet. Use **Pull the latest** in the
   dashboard, or:

   ```bash
   sudo -u domovoi git -C /opt/domovoi pull --ff-only
   ```

3. Install the unit and the grant:

   ```bash
   sudo tee /etc/systemd/system/domovoi-update.service >/dev/null <<'EOF'
   [Unit]
   Description=Domovoi update (back up, sync, migrate, restart, roll back on failure)
   After=docker.service network-online.target
   Wants=network-online.target

   [Service]
   Type=oneshot
   EnvironmentFile=-/etc/default/domovoi-update
   ExecStart=/bin/bash /opt/domovoi/scripts/linux/apply-update.sh
   TimeoutStartSec=30min
   EOF
   echo 'domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block start domovoi-update.service' | sudo tee /etc/sudoers.d/domovoi-update >/dev/null
   sudo chmod 0440 /etc/sudoers.d/domovoi-update
   sudo visudo -c
   sudo systemctl daemon-reload
   ```

   `visudo -c` must report every file `parsed OK`. A broken file under
   `/etc/sudoers.d` can lock you out of `sudo`, so fix it before going on.

4. Apply the pull with the new pipeline. Without `--no-block`,
   `systemctl start` waits until the run finishes:

   ```bash
   sudo systemctl start domovoi-update.service
   cat /var/lib/domovoi-update/last-result.json
   ```

   Expect `"status": "ok"` and `"mode": "update"`. If it says `rolled_back`,
   the box is back on the old SHA and the `error` field says why.
   `journalctl -u domovoi-update -n 200` has the detail. This first run also
   recreates the `domovoi-postgres` container once, to pick up its new
   `restart: unless-stopped` policy. The data volume is untouched.

5. Check the grant the button will use:

   ```bash
   sudo -u domovoi sudo -n -l /usr/bin/systemctl --no-block start domovoi-update.service
   ```

From then on, **Pull the latest** followed by **Restart to apply changes**
runs the whole pipeline, and **last update** in the version panel shows how
it went. The old restart rule in `/etc/sudoers.d/domovoi-restart` can stay.
It's used only if the unit is ever removed or masked.

---

## Two more Linux notes

**Firewall.** If `ufw` is on, satellites need `6370` and browsers need
`6369` from the LAN:

```bash
sudo ufw allow from 192.168.0.0/16 to any port 6369,6370 proto tcp
```

Postgres (`6432`), Letta (`6283`), SearXNG (`6888`) and every room's MPD
*control* port (`6650`, `6651`, …) are published on `127.0.0.1` only, so
no firewall rule is needed to keep them off the LAN — Docker never binds
them to a LAN interface in the first place. The per-room MPD *stream*
ports (`8050`, `8051`, …) are the exception: satellites pull the audio
from them, so they stay LAN-published, and `ufw` must allow them alongside
`6369`/`6370`:

```bash
sudo ufw allow from 192.168.0.0/16 to any port 8050:8099 proto tcp
```

**Rotating the Postgres password.** Postgres reads `POSTGRES_PASSWORD`
only when it initialises the `domovoi-pgdata` volume; after that the
credential lives in the database, so rotating is three steps:

```bash
# 1. change it in Postgres (the container is running)
docker exec -it domovoi-postgres psql -U domovoi -d domovoi \
  -c "ALTER USER domovoi WITH PASSWORD 'new-secret-here';"
# 2. put the same value in domovoi/.env, in BOTH lines:
#      DATABASE_URL=postgresql+asyncpg://domovoi:new-secret-here@localhost:6432/domovoi
#      POSTGRES_PASSWORD=new-secret-here
# 3. restart the core and the web backend (systemctl restart domovoi-core domovoi-web)
```

Flyway picks the new value up from `.env` on its next run (the compose
file interpolates it). Rotate right away if your install predates the
random-password bootstrap — its `.env` carries the template default, and
`python -m domovoi.env_bootstrap` deliberately leaves an existing `.env`
alone.

**Don't suspend.** Desktop-oriented installs sometimes ship with sleep
targets enabled, which is fatal for a machine satellites reconnect to:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```
